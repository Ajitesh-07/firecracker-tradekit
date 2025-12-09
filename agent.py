import socket
import sys
import os
import json
import subprocess
import struct

# --- CONFIG ---
VMADDR_CID_ANY = -1
PORT = 5000
DELIMITER = b"__END__"
MOUNT_POINT = "/mnt/deps"

# We inject the NumpyEncoder directly into the runner script
RUNNER_SCRIPT = r"""
import sys
import json
import os
import traceback
import importlib.util
import numpy as np

# --- 1. Custom Encoder for Rust/NumPy Data ---
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

def load_strategy(path):
    spec = importlib.util.spec_from_file_location("user_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Strategy

def main():
    try:
        try:
            from tradekit_rust import BacktestEngine
        except ImportError:
            print(json.dumps({
                "status": "error", 
                "error": f"Rust Engine not found. PYTHONPATH is: {sys.path}"
            }))
            return

        StrategyClass = load_strategy("/tmp/strategy.py")
        strategy_instance = StrategyClass()
        duration = getattr(strategy_instance, "MAX_DURATION", 30)
        data_path = os.getenv("DATA_PATH", "/code/historical_data")
        
        engine = BacktestEngine(strategy_instance, duration, data_path, 0.0)
        report = engine.run()

        print(json.dumps({"status": "success", "report": report}, cls=NumpyEncoder))

    except Exception:
        print(json.dumps({"status": "error", "error": traceback.format_exc()}))

if __name__ == "__main__":
    main()
"""

def mount_dependencies():
    dep_dev = "/dev/vdb"
    
    if os.path.exists(dep_dev):
        print(f"Found dependency drive at {dep_dev}. Mounting...")
        os.makedirs(MOUNT_POINT, exist_ok=True)
        try:
            # Mount read-only
            subprocess.run(["mount", "-t", "ext4", dep_dev, MOUNT_POINT, "-o", "ro"], check=True)
            print(f"Dependencies mounted at {MOUNT_POINT}")
            return True
        except Exception as e:
            print(f"Failed to mount dependencies: {e}")
            return False
    return False
def main():
    print(f"--- TradeKit Agent listening on VSOCK Port {PORT} ---")

    has_deps = mount_dependencies()
    
    try:
        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    except AttributeError:
        print("Error: AF_VSOCK not supported (Are you running in Firecracker?)")
        return

    s.bind((VMADDR_CID_ANY, PORT))
    s.listen()
    
    while True:
        try:
            conn, addr = s.accept()
            print(f"Connection from Host CID: {addr[0]}")
            
            with conn:
                # --- RECEIVE STRATEGY ---
                full_data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk: break
                    full_data += chunk
                    if DELIMITER in full_data:
                        full_data = full_data.replace(DELIMITER, b"")
                        break
                
                if not full_data: continue

                print("Received Strategy. Writing to /tmp/...")

                with open("/tmp/strategy.py", "w") as f:
                    f.write(full_data.decode('utf-8'))

                with open("/tmp/runner.py", "w") as f:
                    f.write(RUNNER_SCRIPT)

                python_bin = sys.executable
                if not python_bin: python_bin = "/usr/local/bin/python"

                env_vars = os.environ.copy()
                
                # Start with existing python path or default to /code (where your engine is)
                python_path = "/code"

                if has_deps:
                    python_path = f"{MOUNT_POINT}:{python_path}"
                
                env_vars["PYTHONPATH"] = python_path
                
                print(f"DEBUG: Launching runner with PYTHONPATH={python_path}")
                
                print(f"DEBUG: Executing strategy...")
                
                try:
                    proc = subprocess.run(
                        [python_bin, "/tmp/runner.py"],
                        capture_output=True,
                        text=True,
                        timeout=5*60,
                        # env={**os.environ, "PYTHONPATH": "/code"}
                        env=env_vars
                    )
                    
                    if proc.stdout.strip():
                        response_str = proc.stdout
                    else:
                        # Fallback if stdout is empty but stderr has content
                        response_str = json.dumps({
                            "status": "error", 
                            "error": f"Runner Crashed (No Output).\nSTDERR: {proc.stderr}"
                        })

                except subprocess.TimeoutExpired:
                    response_str = json.dumps({"status": "error", "error": "Backtest Timed Out"})
                except Exception as e:
                    response_str = json.dumps({"status": "error", "error": f"Agent Error: {str(e)}"})

                print(f"Sending response ({len(response_str)} bytes)...")
                response_bytes = response_str.encode('utf-8')
                
                conn.sendall(struct.pack("!I", len(response_bytes)))
                
                conn.sendall(response_bytes)
                
        except Exception as e:
            print(f"Agent Loop Error: {e}")

if __name__ == "__main__":
    main()