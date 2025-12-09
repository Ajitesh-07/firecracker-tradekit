import hashlib
import os
import subprocess
import shutil

CACHE_DIR = "./dep_cache"
TEMP_BUILD_DIR = "./temp_build"
DRIVE_SIZE_MB = 256

os.makedirs(CACHE_DIR, exist_ok=True)

def create_dependency_drive(requirements_text: str, log_callback=None) -> str:
    if not requirements_text.strip():
        return None

    req_hash = hashlib.md5(requirements_text.encode('utf-8')).hexdigest()
    image_path = os.path.join(CACHE_DIR, f"{req_hash}.ext4")

    if os.path.exists(image_path):
        if log_callback:
            log_callback(f"Found cached dependencies for hash: {req_hash}")
        return image_path

    if log_callback:
        log_callback(f"Building new dependency drive for hash: {req_hash}")

    build_path = os.path.join(TEMP_BUILD_DIR, req_hash)
    if os.path.exists(build_path):
        shutil.rmtree(build_path)
    os.makedirs(build_path)

    req_file_path = os.path.join(build_path, "requirements.txt")
    with open(req_file_path, "w") as f:
        f.write(requirements_text)

    # 1. PIP INSTALL (Streamed)
    try:
        if log_callback:
            log_callback("Starting pip install...")
        
        pip_cmd = [
            "pip", "install", 
            "-r", req_file_path, 
            "--target", build_path,
            "--no-cache-dir",
            
            # 1. Force fetching Pre-compiled Binaries (No local compiling!)
            "--only-binary=:all:",
            
            # 2. Force Standard Linux (Debian/Ubuntu compatible)
            "--platform", "manylinux2014_x86_64",
            
            # 3. Match your MicroVM Python version EXACTLY
            "--python-version", "3.11",
            
            # 4. Ignore the host machine's environment
            "--implementation", "cp",
            "--abi", "cp311"
        ]

        with subprocess.Popen(
            pip_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            text=True,
            bufsize=1
        ) as proc:
            for line in proc.stdout:
                if log_callback:
                    log_callback(line.strip())
            
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args)

    except subprocess.CalledProcessError as e:
        if log_callback:
            log_callback(f"Pip install failed with code {e.returncode}")
        shutil.rmtree(build_path)
        raise Exception("Failed to install requirements.")

    # 2. CREATE DISK IMAGE (Native Python)
    try:
        if log_callback:
            log_callback("Creating disk image container...")
        
        # Determine size in bytes
        size_bytes = DRIVE_SIZE_MB * 1024 * 1024
        
        # Create empty file of specific size (replaces 'dd')
        with open(image_path, "wb") as f:
            f.truncate(size_bytes)

        if log_callback:
            log_callback("Formatting as ext4 (populating files)...")

        result = subprocess.run(
            ["mkfs.ext4", "-d", build_path, "-F", image_path],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            # Send the actual error to the frontend/logs
            error_msg = f"mkfs.ext4 failed: {result.stderr}"
            if log_callback:
                log_callback(error_msg)
            raise Exception(error_msg)

    except Exception as e:
        if log_callback:
            log_callback(f"Image creation failed: {e}")
        if os.path.exists(image_path): os.remove(image_path)
        raise e
    finally:
        if os.path.exists(build_path):
            shutil.rmtree(build_path)

    if log_callback:
        log_callback(f"Dependency drive ready: {os.path.basename(image_path)}")
        
    return image_path

if __name__ == '__main__':
    def printer(msg): print(f"[STREAM] {msg}")
    create_dependency_drive("rich", log_callback=printer)