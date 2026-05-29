import os
import sys
import json
import time

def handle_sandbox():
    """
    Intercepts script execution inside the container. Pass arguments via JSON,
    but reads stdout/stderr back as raw binary files to guarantee absolute
    character/formatting preservation.
    """
    if not os.environ.get("AM_I_SANDBOXED"):
        return

    script_name = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    
    request_id = f"{script_name.replace('.py', '')}_{int(time.time() * 1000)}"
    
    request_file = f"bridge/requests/{request_id}.json"
    status_file = f"bridge/responses/{request_id}.status"
    stdout_file = f"bridge/responses/{request_id}.stdout"
    stderr_file = f"bridge/responses/{request_id}.stderr"
    
    os.makedirs("bridge/requests", exist_ok=True)
    os.makedirs("bridge/responses", exist_ok=True)
    
    # Write the request data
    with open(request_file, "w") as f:
        json.dump({"script": script_name, "args": args}, f)
        
    # Block and poll until the .status file appears (signals execution completed)
    timeout = 90  # Generous threshold for extensive log parsing
    start_time = time.time()
    while not os.path.exists(status_file):
        if time.time() - start_time > timeout:
            sys.stderr.write(f"Error: Sandbox bridge timeout on {script_name}\n")
            sys.exit(1)
        time.sleep(0.05)
        
    # Give the host system a microsecond to finish writing the file buffers
    time.sleep(0.02)
    
    # Read raw binary bytes and write directly to standard buffers
    if os.path.exists(stdout_file):
        with open(stdout_file, "rb") as f:
            sys.stdout.buffer.write(f.read())
        sys.stdout.buffer.flush()
        
    if os.path.exists(stderr_file):
        with open(stderr_file, "rb") as f:
            sys.stderr.buffer.write(f.read())
        sys.stderr.buffer.flush()
        
    # Read exit code status
    try:
        with open(status_file, "r") as f:
            exit_code = int(f.read().strip())
    except:
        exit_code = 1
        
    # Clean up tracking files safely
    for f_path in [request_file, status_file, stdout_file, stderr_file]:
        try:
            os.remove(f_path)
        except OSError:
            pass
            
    sys.exit(exit_code)