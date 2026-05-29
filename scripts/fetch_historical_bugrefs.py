import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
OPENQA_BASE_URL = "https://openqa.suse.de"

def get_job_bugrefs(job_id):
    comments_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{job_id}/comments"
    try:
        resp = requests.get(comments_url, timeout=10, verify=False)
        resp.raise_for_status()
        comments = resp.json()
        
        bugrefs = set()
        for comment in comments:
            if isinstance(comment, dict) and comment.get("bugrefs"):
                bugrefs.update(comment["bugrefs"])
        return sorted(list(bugrefs))
    except Exception:
        return []

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python fetch_historical_bugrefs.py <job_id1> <job_id2> ..."}))
        sys.exit(1)
        
    # Grab up to 8 unique job IDs
    raw_ids = sys.argv[1:]
    job_ids = list(dict.fromkeys([str(i) for i in raw_ids if i.isdigit()]))[-8:]
    
    results = {}
    total_bugrefs_found = 0
    
    for j_id in job_ids:
        refs = get_job_bugrefs(j_id)
        if isinstance(refs, dict) and "error" in refs:
            results[j_id] = refs     
        elif refs:
            results[j_id] = refs
            total_bugrefs_found += len(refs)
            
    if total_bugrefs_found == 0:
        print(json.dumps({"message": "No bugrefs found in any of the historical jobs."}))
    else:
        print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()