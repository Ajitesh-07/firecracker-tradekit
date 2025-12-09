from flask import Flask, request, jsonify
from flask_cors import CORS
import redis
import uuid
import json
from controller import run_strategy

app = Flask(__name__)
CORS(app)

ALLOWED_EXTENSIONS = {'py'}
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
CACHE_TTL_SECONDS = 600

try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    redis_client.ping()
    print(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except redis.ConnectionError:
    print("WARNING: Redis is not running. Caching will fail.")

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/run', methods=['POST'])
def run_backtest_endpoint():
    print("Received /run Request")
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if file and allowed_file(file.filename):
        try:
            strategy_code = file.read().decode('utf-8')
            full_result = run_strategy(strategy_code)

            if full_result.get('status') == 'error':
                print(full_result)
                return jsonify(full_result), 400

            report = full_result.get('report', {})
            details_map = report.pop('details', {}) 

            session_id = str(uuid.uuid4())
            pipeline = redis_client.pipeline()
            for ticker, data in details_map.items():
                redis_key = f"backtest:{session_id}:{ticker}"
                pipeline.setex(redis_key, CACHE_TTL_SECONDS, json.dumps(data))
            pipeline.execute()
            print(f"Cached data for {len(details_map)} stocks in Redis under Session ID: {session_id}")

            response_payload = {
                "status": "success",
                "session_id": session_id,
                "metrics": report.get('metrics', []),
                "portfolio_summary": report.get('portfolio_summary', {})
            }
            
            return jsonify(response_payload), 200
            
        except Exception as e:
            return jsonify({"error": f"Server Error: {str(e)}"}), 500

    return jsonify({"error": "Invalid file type"}), 400


@app.route('/chart/<session_id>/<ticker>', methods=['GET'])
def get_chart_data(session_id, ticker):
    try:
        redis_key = f"backtest:{session_id}:{ticker}"        
        stored_data = redis_client.get(redis_key)
        
        if not stored_data:
            return jsonify({"error": "Data not found or expired"}), 404
                    
        chart_data = json.loads(stored_data)
        return jsonify(chart_data), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=6767, threaded=True)