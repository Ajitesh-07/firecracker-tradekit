from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis
import asyncio
import uuid
import json
import uvicorn
import aio_pika
from contextlib import asynccontextmanager

# --- CONFIG ---
RABBIT_MQ_URL = "amqp://guest:guest@localhost/"
REDIS_URL = "redis://localhost"
TASK_QUEUE_NAME = "backtest_tasks"
PUB_SUB_CHANNEL = "backtest_updates"

# --- BACKGROUND LISTENER ---
async def redis_pubsub_listener(app: FastAPI):
    redis_conn = None
    try:
        # We need a dedicated connection for PubSub (it blocks)
        redis_conn = redis.from_url(REDIS_URL, decode_responses=True)
        async with redis_conn.pubsub() as pubsub:
            await pubsub.subscribe(PUB_SUB_CHANNEL)
            print(f"[System] Subscribed to Redis channel: {PUB_SUB_CHANNEL}")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data_str = message["data"]
                    try:
                        data = json.loads(data_str)
                        task_id = data.get("task_id")
                        
                        if not task_id:
                            continue
                        
                        # DEBUG LOG: See who is currently connected
                        # print(f"[Debug] Msg for {task_id}. Active Sockets: {list(app.state.websockets.keys())}")

                        websocket = app.state.websockets.get(task_id)
                        
                        if websocket:
                            await websocket.send_json(data)
                        else:
                            # If socket not found, we don't worry too much 
                            # because we are now caching status in the Worker (see below)
                            pass 
                            
                    except json.JSONDecodeError:
                        print(f"Error decoding JSON: {data_str}")
                    except Exception as e:
                        print(f"Error relaying to websocket: {e}")
                        
    except (redis.ConnectionError, asyncio.CancelledError) as e:
        print(f"Redis Pub/Sub listener disconnected: {e}")
    finally:
        if redis_conn:
            await redis_conn.close()

# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[System] Starting up...")
    
    # Global dictionary to hold active connections
    app.state.websockets = {}

    # Connect RabbitMQ
    try:
        connection = await aio_pika.connect_robust(RABBIT_MQ_URL)
        channel = await connection.channel()
        await channel.declare_queue(TASK_QUEUE_NAME, durable=True)
        app.state.rabbitmq_connection = connection
        app.state.rabbitmq_channel = channel
        print("[System] Connected to RabbitMQ.")
    except Exception as e:
        print(f"[Error] RabbitMQ Connection failed: {e}")

    # Connect Redis (General Use)
    try:
        redis_pool = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_pool.ping()
        app.state.redis = redis_pool
        print("[System] Connected to Redis.")
    except Exception as e:
        print(f"[Error] Redis Connection failed: {e}")

    # Start Background Listener
    app.state.pubsub_task = asyncio.create_task(redis_pubsub_listener(app))

    yield

    # Cleanup
    print("[System] Shutting down...")
    if hasattr(app.state, 'rabbitmq_connection'):
        await app.state.rabbitmq_connection.close()
    if hasattr(app.state, 'redis'):
        await app.state.redis.close()

app = FastAPI(title="Velora Developer API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENDPOINTS ---

@app.post("/run")
async def run_strategy_endpoint(file: UploadFile = File(...), requirement: UploadFile = File(None)):
    if not file.filename.endswith('.py'):
        raise HTTPException(status_code=400, detail="Only .py files allowed")
    
    if requirement == None:
        requirements = ""
    else:
        if not requirement.filename.endswith('.txt'):
            raise HTTPException(status_code=400, detail="Only .txt requirements")
        requirements = (await requirement.read()).decode('utf-8')

    content = await file.read()
    strategy_code = content.decode('utf-8')
    
    task_id = uuid.uuid4().hex
    
    # 1. Clear any old status in Redis for this ID (just in case)
    redis_client = app.state.redis
    await redis_client.delete(f"task_status:{task_id}")

    # 2. Queue the Task
    task_message = json.dumps({
        "task_id": task_id,
        "code": strategy_code,
        "requirements": requirements
    })

    try:
        channel = app.state.rabbitmq_channel
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=task_message.encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=TASK_QUEUE_NAME
        )
        print(f" [x] Queued Task: {task_id}")
    except Exception as e:
        print(f"Failed to publish to RabbitMQ: {e}")
        raise HTTPException(status_code=503, detail="Task broker unavailable")

    return {
        "status": "queued",
        "task_id": task_id,
        "websocket_url": f"ws://localhost:5000/ws/{task_id}",
        "message": "Strategy queued."
    }

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()

    # 1. Register Connection
    websocket.app.state.websockets[task_id] = websocket
    print(f"[WS] Client connected: {task_id}")

    try:
        redis_client = websocket.app.state.redis
        
        # Check for the latest status stored by the Worker
        cached_status = await redis_client.get(f"task_status:{task_id}")
        
        if cached_status:
            print(f"[WS] Found cached status for {task_id}, sending immediately.")
            await websocket.send_json(json.loads(cached_status))
        else:
            # Send an initial 'Connected' message so Frontend knows pipe is open
            await websocket.send_json({
                "task_id": task_id,
                "status": "processing",
                "message": "Connected to stream. Waiting for worker..."
            })

    except Exception as e:
        print(f"Error on initial check: {e}")

    # 3. Keep Connection Open
    try:
        while True:
            await websocket.receive_text() 
    except WebSocketDisconnect:
        print(f"[WS] Client disconnected: {task_id}")
    finally:
        if task_id in websocket.app.state.websockets:
            del websocket.app.state.websockets[task_id]

@app.get("/chart/{task_id}/{ticker}")
async def get_chart_data(task_id: str, ticker: str):
    try:
        redis_client = app.state.redis
        redis_key = f"backtest:{task_id}:{ticker}"
        
        data_str = await redis_client.get(redis_key)
        
        if not data_str:
            raise HTTPException(status_code=404, detail="Chart data expired or not found")
            
        return json.loads(data_str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=5000)