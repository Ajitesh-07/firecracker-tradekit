import pika
import redis
import json
import uuid
from controller import run_strategy
from builder import create_dependency_drive

# --- CONFIG ---
RABBIT_HOST = 'localhost'
REDIS_HOST = 'localhost'
QUEUE_NAME = 'backtest_tasks'
PUB_SUB_CHANNEL = 'backtest_updates'
CACHE_TTL = 600

redis_client = redis.Redis(host=REDIS_HOST, port=6379, db=0)

def process_job(ch, method, properties, body):
    try:
        data = json.loads(body)
        task_id = data['task_id']
        strategy_code = data['code']
        requirements = data['requirements']

        print(f" [x] Processing Task: {task_id}")

        start_payload = {
            "task_id": task_id, 
            "status": "processing", 
            "message": "Booting MicroVM..."
        }

        def log_callback(message):
            start_payload = {
                "task_id": task_id, 
                "status": "processing", 
                "message": message
            }

            redis_client.setex(f"task_status:{task_id}", 600, json.dumps(start_payload))
            redis_client.publish(PUB_SUB_CHANNEL, json.dumps(start_payload))


        redis_client.setex(f"task_status:{task_id}", 600, json.dumps(start_payload))
        redis_client.publish(PUB_SUB_CHANNEL, json.dumps(start_payload))

        dependency_url = create_dependency_drive(requirements, log_callback)

        full_result = run_strategy(task_id, strategy_code, log_callback, dependency_url)

        if full_result.get('status') == 'error':
            error_payload = {
                "task_id": task_id,
                "status": "error",
                "message": full_result.get('error', 'Unknown Error'),
                "traceback": full_result.get('traceback')
            }
            redis_client.publish(PUB_SUB_CHANNEL, json.dumps(error_payload))
        else:
            report = full_result.get('report', {})
            
            details_map = report.pop('details', {}) 

            pipeline = redis_client.pipeline()
            for ticker, chart_data in details_map.items():
                redis_key = f"backtest:{task_id}:{ticker}"
                pipeline.setex(redis_key, CACHE_TTL, json.dumps(chart_data))
            pipeline.execute()

            success_payload = {
                "task_id": task_id,
                "status": "success",
                "metrics": report.get('metrics', []),
                "portfolio_summary": report.get('portfolio_summary', {})
            }

            redis_client.publish(PUB_SUB_CHANNEL, json.dumps(success_payload))
            print(f" [✓] Task {task_id} Completed & Published")

    except Exception as e:
        print(f" [!] Worker Error: {e}")

    ch.basic_ack(delivery_tag=method.delivery_tag)

def start_worker():
    while True:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBIT_HOST))
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=process_job)

            print(' [*] Worker started. Waiting for messages...')
            channel.start_consuming()
        except Exception as e:
            print(f"Connection lost, retrying... {e}")

if __name__ == '__main__':
    start_worker()