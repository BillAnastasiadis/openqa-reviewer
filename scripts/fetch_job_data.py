import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import re
import os
import hashlib
from nostril_detector import nonsense
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from sanitizer import sanitize_text

OPENQA_BASE_URL = "https://openqa.suse.de"
SMELT_GRAPHQL_ENDPOINT = "https://smelt.suse.de/graphql"

# openQA Data Fetching Functions
def extract_incident_ids(settings):
    incident_ids = set()
    for key, value in settings.items():
        if not isinstance(value, str):
            continue
        if "_ISSUES" in key:
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if all(part.isdigit() for part in parts):
                for part in parts:
                    incident_ids.add(int(part))
        elif key == "INCIDENT_ID" and value.isdigit():
            incident_ids.add(int(value))
    return list(incident_ids)

def fetch_packages_for_incident(incident_id, token=None):
    query = f"""
        query getIncidentPackages {{
          incidents(incidentId: {incident_id}) {{
            edges {{
              node {{
                packages {{
                  edges {{
                    node {{
                      name
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
    """
    params = {"query": query, "operationName": "getIncidentPackages", "variables": "{}"}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(SMELT_GRAPHQL_ENDPOINT, params=params, headers=headers, timeout=20, verify=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [f"Error fetching packages for {incident_id}: {str(e)}"]

    package_names = set()
    incidents = data.get("data", {}).get("incidents", {})
    edges = incidents.get("edges", []) or []

    for edge in edges:
        node = edge.get("node", {})
        packages = node.get("packages", {})
        pkg_edges = packages.get("edges", []) or []
        for pkg_edge in pkg_edges:
            pkg_node = pkg_edge.get("node", {})
            name = pkg_node.get("name")
            if name:
                package_names.add(name)

    return sorted(list(package_names))

def get_job_details(job_id):
    api_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{job_id}"
    response = requests.get(api_url, verify=False, timeout=45)
    response.raise_for_status()
    data = response.json()
    
    job_data = data.get('job', {})
    settings = job_data.get('settings', {})
    incident_ids = extract_incident_ids(settings)
    test = settings.get('TEST')
        
    details_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{job_id}/details"
    details_response = requests.get(details_url, verify=False, timeout=45)
    details_response.raise_for_status()
    result = details_response.json().get('job', {}).get('result')
    reason_for_result = details_response.json().get('job', {}).get('reason')

    testresults = details_response.json().get('job', {}).get('testresults', [])
    
    all_modules = [mod.get('name', 'Unknown') for mod in testresults]
    
    # Grab failing module and its chronological execution steps
    failing_module = "Unknown"
    failing_module_execution_steps = []
    
    for mod in testresults:
        if mod.get('result') in ['failed', 'died', 'incomplete']:
            failing_module = mod.get('name', 'Unknown')
            module_details = mod.get('details', [])
            
            # Find the exact step where the failure occurred
            fail_index = -1
            for i, step in enumerate(module_details):
                if step.get('result') in ['fail', 'failed', 'died']:
                    fail_index = i
                    break
            
            # Fallback: if no specific step is marked failed, get the last
            if fail_index == -1:
                fail_index = len(module_details) - 1
                
            # Grab the 10 entries above it, PLUS the failing entry itself
            start_index = max(0, fail_index - 10)
            target_steps = module_details[start_index:fail_index + 1]
            
            for step in target_steps:
                text_data = step.get('text_data')
                if text_data:
                    failing_module_execution_steps.append({
                        "step_num": step.get('num'),
                        "command": step.get('title'),
                        "result": step.get('result'),
                        "output": sanitize_text(text_data.strip())
                    })
            break # found the culprit, stop looking at other modules

    # Fetch vars.json to get TEST_GIT_HASH
    vars_url = f"{OPENQA_BASE_URL}/tests/{job_id}/file/vars.json"
    test_git_hash = None
    try:
        vars_response = requests.get(vars_url, timeout=10, verify=False)
        vars_response.raise_for_status()
        vars_data = vars_response.json()
        test_git_hash = vars_data.get('TEST_GIT_HASH')
    except Exception as e:
        # If the file doesn't exist or download fails, default to None
        pass
    
    last_good_test_info = get_last_known_good_test_hash(job_id, settings)

    return {
        "job_name": job_data.get('name'),
        "result": result,
        "test": test,
        "reason_for_result": reason_for_result,
        "job_group": job_data.get('group'),
        "arch": settings.get('ARCH'),
        "version": settings.get('VERSION'),
        "test_git_hash": test_git_hash,
        "incident_ids": incident_ids,
        "all_modules": all_modules,
        "failing_module": failing_module,
        "failing_module_execution_steps": failing_module_execution_steps,
        "last_good_test": last_good_test_info
    }

def get_error_trace(job_id):
    log_url = f"{OPENQA_BASE_URL}/tests/{job_id}/file/autoinst-log.txt"
    response = requests.get(log_url, verify=False, timeout=45)
    response.raise_for_status()
    
    lines = response.text.splitlines()
    trace_lines = []
    capture = False
    
    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if "Test died:" in line or "fatal error" in line.lower():
            capture = True
            
        if capture:
            trace_lines.append(line)
            if len(trace_lines) > 1 and re.match(r'^\[\d{4}-\d{2}-\d{2}T', line) and "called at" not in line:
                break
                
    raw_trace = "\n".join(trace_lines)
    return sanitize_text(raw_trace, trace=True)

def fetch_failing_module_metadata(error_trace, test_git_hash):
    """Parses the error trace for the failing test file paths without downloading the code."""
    if not test_git_hash:
        return {"error": "TEST_GIT_HASH not found in job settings."}

    regex = re.compile(r'at\s+([^\s]+\.pm)\s+line\s+(\d+)')
    
    first_relative_path = None
    first_line_number = None
    relevant_file_paths = []

    for line in error_trace.splitlines():
        if '/os-autoinst/' in line or 'perl5/vendor_perl' in line:
            continue
            
        match = regex.search(line)
        if match:
            full_path = match.group(1)
            
            if 'tests/' in full_path:
                relative_path = 'tests/' + full_path.split('tests/')[-1]
            elif 'lib/' in full_path:
                relative_path = 'lib/' + full_path.split('lib/')[-1]
            else:
                relative_path = full_path

            if relative_path not in relevant_file_paths:
                relevant_file_paths.append(relative_path)
            
            if first_relative_path is None:
                first_relative_path = relative_path
                first_line_number = int(match.group(2))

    if not first_relative_path:
        return {"error": "Could not find a valid '.pm' file path in the error trace."}

    return {
        "file_path": first_relative_path,
        "failed_at_line": first_line_number,
        "github_web_url": f"https://github.com/os-autoinst/os-autoinst-distri-opensuse/blob/{test_git_hash}/{first_relative_path}#L{first_line_number}",
        "relevant_file_paths": relevant_file_paths 
    }

def get_job_bugrefs(job_id):
    """Fetches comments for the job and extracts any bug references."""
    comments_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{job_id}/comments"
    try:
        resp = requests.get(comments_url, timeout=20, verify=False)
        resp.raise_for_status()
        comments = resp.json()
        
        bugrefs = set()
        for comment in comments:
            if isinstance(comment, dict) and comment.get("bugrefs"):
                bugrefs.update(comment["bugrefs"])
        return sorted(list(bugrefs))
    except Exception as e:
        # If it fails, return empty list
        return []

def get_last_known_good_test_hash(current_job_id, settings):
    """
    Finds the last known good test for the given scenario and retrieves its TEST_GIT_HASH.
    Uses scenario identifiers to query the API for previous 'passed' jobs.
    """
    # Keys that define the exact scenario in openQA
    scenario_keys = ['TEST', 'FLAVOR', 'ARCH', 'MACHINE', 'DISTRI', 'VERSION']
    
    params = {
        "result": "passed",
        "limit": 100  # debatable
    }
    
    # create the query with the scenario settings
    for key in scenario_keys:
        if settings.get(key):
            params[key.lower()] = settings[key]
            
    api_url = f"{OPENQA_BASE_URL}/api/v1/jobs"
    try:
        response = requests.get(api_url, params=params, verify=False, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return {"error": f"API query for last known good test failed: {str(e)}"}
        
    jobs = data.get('jobs', [])
    
    # Filter for passed jobs that ran before the current job
    # (we pretend that comparing IDs acts as a reliable chronological check)
    valid_jobs = []
    for j in jobs:
        try:
            job_id_int = int(j['id'])
            if job_id_int < int(current_job_id):
                valid_jobs.append(job_id_int)
        except (ValueError, KeyError):
            continue
            
    if not valid_jobs:
        return {"error": "No previous passed job found for this scenario within the queried limit."}
        
    #max ID ~ the most recent pass before the failure
    last_good_job_id = max(valid_jobs)
    
    # Now fetch vars.json for this last known good job to get the hash
    vars_url = f"{OPENQA_BASE_URL}/tests/{last_good_job_id}/file/vars.json"
    try:
        vars_response = requests.get(vars_url, timeout=20, verify=False)
        vars_response.raise_for_status()
        vars_data = vars_response.json()
        
        return {
            "job_id": last_good_job_id,
            "test_git_hash": vars_data.get('TEST_GIT_HASH'),
            "url": f"{OPENQA_BASE_URL}/tests/{last_good_job_id}"
        }
    except Exception as e:
        return {
            "job_id": last_good_job_id,
            "error": f"Failed to fetch vars.json for last good job {last_good_job_id}: {str(e)}"
        }

def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_job_data.py <job_id>")
        sys.exit(1)
        
    job_id = sys.argv[1]
    smelt_token = os.getenv("SMELT_TOKEN")
    
    try:
        job_info = get_job_details(job_id)
        job_info['error_trace'] = get_error_trace(job_id)
        job_info['bugrefs'] = get_job_bugrefs(job_id)
        
        job_info['failing_code_context'] = fetch_failing_module_metadata(
            job_info['error_trace'], 
            job_info.get('test_git_hash')
        )
        
        incident_packages = {}
        for incident_id in job_info['incident_ids']:
            incident_packages[str(incident_id)] = fetch_packages_for_incident(incident_id, token=smelt_token)
            
        job_info['incident_packages'] = incident_packages
                    
        os.makedirs("temp_data", exist_ok=True)
        cache_path = os.path.join("temp_data", f"{job_id}.json")
        with open(cache_path, "w") as f:
            json.dump(job_info, f)
        
        print(json.dumps(job_info, indent=2))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()