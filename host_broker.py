import os
import json
import time
import subprocess

REQUESTS_DIR = "bridge/requests"
RESPONSES_DIR = "bridge/responses"

# HARDCODED ALLOWLIST: The broker will ONLY execute these exact file names.
# No path traversal, no arbitrary binaries, and no external scripts.
ALLOWED_SCRIPTS = {
    "fetch_job_data.py",
    "trace_functions.py",
    "compare_commits.py",
    "analyze_logs.py",
    "osado_lib.py",
    "fetch_historical_packages.py",
    "check_pkg_versions.py",
    "fetch_historical_bugrefs.py",
    "analyze_aggregate_incidents.py"
}

os.makedirs(REQUESTS_DIR, exist_ok=True)
os.makedirs(RESPONSES_DIR, exist_ok=True)

print("Hardened Air-Gapped Host Broker Daemon Active.")
print("Watching for verified container requests... (Press Ctrl+C to stop)")

while True:
    try:
        for filename in os.listdir(REQUESTS_DIR):
            if filename.endswith(".json"):
                req_path = os.path.join(REQUESTS_DIR, filename)
                base_id = filename.replace(".json", "")
                
                status_path = os.path.join(RESPONSES_DIR, f"{base_id}.status")
                stdout_path = os.path.join(RESPONSES_DIR, f"{base_id}.stdout")
                stderr_path = os.path.join(RESPONSES_DIR, f"{base_id}.stderr")
                
                time.sleep(0.02)
                if not os.path.exists(req_path):
                    continue
                    
                try:
                    with open(req_path, "r") as f:
                        req_data = json.load(f)
                    
                    script = req_data.get("script", "")
                    args = req_data.get("args", [])
                    
                    # SECURITY GATEWAY: Validate the script string strictly
                    if script not in ALLOWED_SCRIPTS:
                        print(f"🚨 SECURITY ALERT: Unauthorized script execution blocked: '{script}'")
                        with open(stdout_path, "wb") as f:
                            f.write(b"")
                        with open(stderr_path, "wb") as f:
                            f.write(b"Security Block: Unauthorized script target execution attempted.")
                        with open(status_path, "w") as f:
                            f.write("1")
                        if os.path.exists(req_path):
                            os.remove(req_path)
                        continue
                    
                    print(f"📥 [CONTAINER - VERIFIED] Running: {script}")
                    
                    env = os.environ.copy()
                    if "AM_I_SANDBOXED" in env:
                        del env["AM_I_SANDBOXED"]
                        
                    cmd = ["gemini_env/bin/python3", f"scripts/{script}"] + args
                    result = subprocess.run(cmd, capture_output=True, env=env)
                    
                    with open(stdout_path, "wb") as f:
                        f.write(result.stdout)
                        
                    with open(stderr_path, "wb") as f:
                        f.write(result.stderr)
                        
                    with open(status_path, "w") as f:
                        f.write(str(result.returncode))
                        
                    print(f"[HOST] Processed verified request '{script}' (Code: {result.returncode})")
                    
                except Exception as e:
                    print(f"Error processing request {filename}: {e}")
                    with open(stdout_path, "wb") as f:
                        f.write(b"")
                    with open(stderr_path, "wb") as f:
                        f.write(f"Host Broker Exception: {str(e)}".encode('utf-8'))
                    with open(status_path, "w") as f:
                        f.write("1")
                
                # Delete request packet after evaluation
                if os.path.exists(req_path):
                    try:
                        os.remove(req_path)
                    except OSError:
                        pass
                        
    except Exception as e:
        print(f"Daemon Loop Error: {e}")
        
    time.sleep(0.1)