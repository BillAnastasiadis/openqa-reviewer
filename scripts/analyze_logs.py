import proxy_helper
proxy_helper.handle_sandbox()

import time
import sys
import json
import argparse
import os
import tempfile
import re
import hashlib
from collections import deque

from sanitizer import sanitize_text

try:
    from openqa_log_local import openQA_log_local
except ImportError:
    print(json.dumps({"error": "The openqa_log_local module is not installed."}))
    sys.exit(1)

def search_in_file(filepath, query, max_matches=3, context_lines=2):
    matches = []
    total_found = 0
    
    try:
        regex = re.compile(query, re.IGNORECASE)
    except re.error as e:
        return {"error": f"Invalid search pattern '{query}': {str(e)}"}
    
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        before_buffer = deque(maxlen=context_lines)
        capture_after = 0
        current_match_block = []
        current_match_line_num = 0
        
        for line_num, line in enumerate(f, 1):
            is_match = bool(regex.search(line))
            
            if is_match:
                total_found += 1
                if len(matches) < max_matches:
                    # If already capturing a match, resolve it
                    if capture_after > 0:
                        matches.append({
                            "line_number": current_match_line_num,
                            "snippet": sanitize_text("".join(current_match_block).strip())
                        })
                        current_match_block = []
                        
                    # new capture block (including previous context + current line)
                    current_match_block = list(before_buffer) + [line]
                    current_match_line_num = line_num
                    capture_after = context_lines
            else:
                if capture_after > 0:
                    current_match_block.append(line)
                    capture_after -= 1
                    # after capturing the 'after' context, save it
                    if capture_after == 0:
                        matches.append({
                            "line_number": current_match_line_num,
                            "snippet": sanitize_text("".join(current_match_block).strip())
                        })
                        current_match_block = []
                
            # Always keep a buffer of the previous lines
            before_buffer.append(line)
            
        # If eof while still capturing 'after' context
        if capture_after > 0 and len(matches) < max_matches:
            matches.append({
                "line_number": current_match_line_num,
                "snippet": sanitize_text("".join(current_match_block).strip())
            })

    result = {
        "query": query,
        "total_found": total_found,
        "matches_shown": len(matches),
        "matches": matches
    }
    
    if total_found > max_matches:
        result["warning"] = f"Found {total_found} occurrences. Output bottled to {max_matches}. Refine query if needed."
        
    return result

def enforce_execution_limit(job_id, max_runs=15):
    """Enforces a hard execution limit per job_id to prevent agent loops."""
    cache_file = os.path.join(tempfile.gettempdir(), f".openqa_log_limit_{job_id}.json")
    current_time = time.time()
    count = 0
    
    # if the cache file is older than 10m, reset count
    if os.path.exists(cache_file):
        if current_time - os.path.getmtime(cache_file) > 20:
            os.remove(cache_file)
        else:
            try:
                with open(cache_file, 'r') as f:
                    count = json.load(f).get('count', 0)
            except Exception:
                pass
                
    if count >= max_runs:
        return False
        
    # Increment+save
    try:
        with open(cache_file, 'w') as f:
            json.dump({'count': count + 1}, f)
    except Exception:
        pass
        
    return True

def main():
    parser = argparse.ArgumentParser(description="Securely inspect openQA log files.")
    parser.add_argument("job_id", help="The openQA job ID.")
    parser.add_argument("--list", action="store_true", help="List available log files.")
    parser.add_argument("--search", help="The filename to download and search (e.g., 'worker-log.txt').")
    parser.add_argument("--query", help="The substring to search for within the target log.")
    
    args = parser.parse_args()

    if not enforce_execution_limit(args.job_id, max_runs=15):
        print(json.dumps({
            "error": "CRITICAL SYSTEM LIMIT REACHED.",
            "message": "You have executed analyze_logs.py 15 times. Resource quota exceeded.",
            "directive": "ABORT log investigation immediately. You MUST proceed to Step 6 now."
        }, indent=2))
        sys.exit(0)
    
    # Initialize connection (disabling stdout noise)
    original_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        oll = openQA_log_local(host='openqa.suse.de')
    finally:
        sys.stdout.close()
        sys.stdout = original_stdout
        
    if args.list:
        try:
            logs = oll.get_log_list(job_id=args.job_id)
            print(json.dumps({"available_logs": logs}, indent=2))
        except Exception as e:
            print(json.dumps({"error": f"Failed to list logs: {str(e)}"}))
        sys.exit(0)
        
    if args.search and args.query:
        temp_dir = tempfile.gettempdir()
        dest_path = os.path.join(temp_dir, f"{args.job_id}_{args.search}")
        
        try:
            oll.client.download_log_to_file_1(job_id=args.job_id, filename=args.search, destination_path=dest_path)
            
            # Analysis
            search_results = search_in_file(dest_path, args.query)
            print(json.dumps(search_results, indent=2))
            
        except Exception as e:
            print(json.dumps({"error": f"Failed to fetch or read log: {str(e)}"}))
            
        finally:
            # cleanup
            if os.path.exists(dest_path):
                os.remove(dest_path)
                
        sys.exit(0)
        
    print(json.dumps({"error": "Invalid arguments. Use --list OR --search with --query."}))

if __name__ == "__main__":
    main()