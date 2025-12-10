import socket
import subprocess
import os
import time
import json
import struct
import requests_unixsocket

KERNEL_PATH = "./vmlinux.bin"
ROOTFS_PATH = "./rootfs.ext4"
AGENT_PORT = 5000

def recvall(sock, n):
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def run_strategy(task_id, user_strategy, log_callback, dependency_image_path=None):

    api_socket = f"/tmp/firecracker_{task_id}.socket"
    vsock_path = f"/tmp/v_{task_id}.sock"
    log_file = f"vm_{task_id}.log"

    unique_cid = 3 + (int(task_id[-8:], 16) % 1000000)

    # 1. Cleanup old sockets (specific to this task only)
    for path in [api_socket, vsock_path]:
        if os.path.exists(path): os.remove(path)

    print(f"[Host] Starting Firecracker for Task {task_id}...")
    log_fp = open(log_file, "w")
    
    fc_proc = subprocess.Popen(
        ["./firecracker", "--api-sock", api_socket],
        stdout=log_fp,
        stderr=log_fp
    )

    client_sock = None

    try:
        # 2. Wait for Firecracker API Socket
        while not os.path.exists(api_socket):
            time.sleep(0.1)
            if fc_proc.poll() is not None:
                return {"status": "error", "type": "BootError", "message": "Firecracker exited immediately. Check vm.log."}

        session = requests_unixsocket.Session()
        base_url = f"http+unix://{api_socket.replace('/', '%2F')}"

        # 3. Configure VM via API
        print("[Host] Configuring MicroVM...")
        st = time.time()
        try:
            # Basic Config
            session.put(f"{base_url}/machine-config", json={"vcpu_count": 1, "mem_size_mib": 256, "smt": False}).raise_for_status()
            session.put(f"{base_url}/boot-source", json={"kernel_image_path": KERNEL_PATH, "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/sbin/myinit"}).raise_for_status()
            session.put(f"{base_url}/drives/rootfs", json={"drive_id": "rootfs", "path_on_host": ROOTFS_PATH, "is_root_device": True, "is_read_only": True}).raise_for_status()
            if dependency_image_path:
                print(f"[Host] Attaching Dependency Drive: {dependency_image_path}")
                session.put(f"{base_url}/drives/deps", json={
                    "drive_id": "deps",
                    "path_on_host": dependency_image_path,
                    "is_root_device": False,
                    "is_read_only": True  # Safety: VM cannot corrupt the cache
                }).raise_for_status()

            # VSOCK Config (Host UDS Path)
            session.put(f"{base_url}/vsock", json={"guest_cid": unique_cid, "uds_path": vsock_path}).raise_for_status()
            
            # Boot
            session.put(f"{base_url}/actions", json={"action_type": "InstanceStart"}).raise_for_status()
        except Exception as e:
            return {"status": "error", "type": "ConfigError", "message": f"API Error: {str(e)}"}

        log_callback(f"Booted Up VM in {round((time.time() - st)*1000)}ms")
        # 4. Connect to Agent via VSOCK UDS
        print("[Host] Connecting to Agent inside VM...")
        connected = False
        while not connected:
            try:
                client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client_sock.connect(vsock_path)
                
                # Firecracker UDS Handshake: "CONNECT <PORT>\n"
                client_sock.sendall(f"CONNECT {AGENT_PORT}\n".encode())
                
                # Wait for "OK <CID>" response
                ack = client_sock.recv(1024)
                if b"OK" in ack:
                    connected = True
                    break
                else:
                    client_sock.close()
            except:
                if client_sock: client_sock.close()
                time.sleep(0.2)
        
        if not connected:
            return {"status": "error", "type": "ConnectionError", "message": "Timed out connecting to Agent."}

        # 5. Send Strategy
        print("[Host] Sending Strategy Payload...")
        # Add delimiter so Agent knows when to stop reading
        payload = user_strategy.encode() + b"__END__"
        client_sock.sendall(payload)

        # 6. RECEIVE RESPONSE (The Critical Fix)
        print("[Host] Waiting for execution result...")
        log_callback("Executing Backtesting..")
        client_sock.settimeout(300) # 5 Minute timeout

        # A. Read the first 4 bytes (Length Header)
        raw_len = recvall(client_sock, 4)
        if not raw_len:
            return {"status": "error", "type": "ProtocolError", "message": "Connection closed before receiving length header."}
        
        # Unpack big-endian unsigned int
        msg_length = struct.unpack('!I', raw_len)[0]
        log_callback("Backtest Completed Compiling Results..")
        print(f"[Host] Expecting {msg_length} bytes of JSON data...")

        # B. Read the exact number of bytes
        response_bytes = recvall(client_sock, msg_length)
        if not response_bytes:
            return {"status": "error", "type": "ProtocolError", "message": "Connection closed while reading payload."}

        # 7. Parse Result
        try:
            res_json = json.loads(response_bytes.decode('utf-8'))
            return res_json
        except json.JSONDecodeError:
            print(response_bytes.decode('utf-8'))
            return {"status": "error", "type": "JSONError", "message": "Invalid JSON received", "preview": str(response_bytes[:100])}

    except Exception as e:
        return {"status": "error", "type": "HostError", "message": str(e)}

    finally:
        # 8. Cleanup
        if client_sock: client_sock.close()
        
        # Kill Firecracker
        if fc_proc.poll() is None:
            fc_proc.kill()
            fc_proc.wait()
            
        if not log_fp.closed: log_fp.close()
        
        # Remove sockets
        if os.path.exists(api_socket): os.remove(api_socket)
        if os.path.exists(vsock_path): os.remove(vsock_path)
        if os.path.exists(log_file): os.remove(log_file)

if __name__ == "__main__":
    bad_strategy = """
import random
this_is_syntax_error int x = 4; 
"""
    
    good_strategy = """
import random
import rich
    
class Strategy:
    def step(self, h, _):
        x = random.randint(0, 2) - 1
        return x
"""

    print("--- Running Test ---")
    from builder import create_dependency_drive
    result = run_strategy(good_strategy, lambda x: {}, "./dep_cache/e7f4f8bd246c235418280d1f124e14f0.ext4")
    
    if "report" in result:
        print("\nSuccess! Report Keys:", result["report"].keys())
        print("Details for first stock:", list(result["report"]["details"].keys()) if "details" in result["report"] else "No details")
    else:
        print("\nError Result:", json.dumps(result, indent=2))