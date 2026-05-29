import proxy_helper
proxy_helper.handle_sandbox()

import argparse
import requests
import json
import os
import sys
from collections import Counter
from sanitizer import sanitize_text
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENQA_BASE_URL = "https://openqa.suse.de"
API_TIMEOUT = 45

def extract_top_10(text):
    lines = text.strip().splitlines()
    if len(lines) > 10:
        return "\n".join(lines[:10]) + "\n... [truncated to 10 lines]"
    return "\n".join(lines)

def extract_incident_string(settings):
    """
    Safely extracts INCIDENT_ID and *_ISSUES (like TEST_ISSUES[]) from settings
    and returns a sorted, comma-separated string for easy agent reading.
    """
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
            
    if not incident_ids:
        return "None"
    return ",".join(map(str, sorted(list(incident_ids))))

def analyze_similar_failures(job_id, failing_module, test_name, current_incident, current_version, current_arch):
    
    def get_setting(job_data, key, default="None"):
        return str(job_data.get('settings', {}).get(key, default))

    def target_module_failed(job_data):
        for mod in job_data.get('modules', []):
            if mod.get('name') == failing_module and mod.get('result') in ['failed', 'died', 'incomplete']:
                return True
        return False

    # Get last 150 failed jobs & 50 passed jobs for this TEST
    try:
        failed_jobs_raw = requests.get(
            f"{OPENQA_BASE_URL}/api/v1/jobs", 
            params={"test": test_name, "result": "failed", "limit": 150},
            verify=False, timeout=API_TIMEOUT
        ).json().get('jobs', [])
        
        passed_jobs_raw = requests.get(
            f"{OPENQA_BASE_URL}/api/v1/jobs", 
            params={"test": test_name, "result": "passed", "limit": 50},
            verify=False, timeout=API_TIMEOUT
        ).json().get('jobs', [])
    except Exception as e:
        return {"error": f"API query failed: {str(e)}"}

    # Get jobs specifically for this INCIDENT_ID + TEST (only applies if current is a single incident)
    incident_jobs = []
    if current_incident and current_incident.lower() != 'none':
        try:
            incident_jobs = requests.get(
                f"{OPENQA_BASE_URL}/api/v1/jobs", 
                params={"test": test_name, "job_setting": f"INCIDENT_ID={current_incident}", "limit": 100},
                verify=False, timeout=API_TIMEOUT
            ).json().get('jobs', [])
        except Exception:
            pass 

    # Filter out current job
    failed_jobs_raw = [j for j in failed_jobs_raw if str(j['id']) != str(job_id)]
    passed_jobs_raw = [j for j in passed_jobs_raw if str(j['id']) != str(job_id)]
    incident_jobs = [j for j in incident_jobs if str(j['id']) != str(job_id)]
    
    matching_fails = [j for j in failed_jobs_raw if target_module_failed(j)]
    
    # Use extract_incident_string for distribution counting
    fail_incidents = Counter([extract_incident_string(j.get('settings', {})) for j in matching_fails])
    fail_versions = Counter([get_setting(j, 'VERSION') for j in matching_fails])
    fail_arches = Counter([get_setting(j, 'ARCH') for j in matching_fails])

    inc_passed = [j for j in incident_jobs if j.get('result') == 'passed']
    inc_failed = [j for j in incident_jobs if j.get('result') == 'failed']
    
    # Get 6 newest AND 6 oldest matching fails
    if len(matching_fails) <= 12:
        jobs_to_detail = matching_fails
    else:
        jobs_to_detail = matching_fails[:6] + matching_fails[-6:]
    
    detailed_failing_jobs = []
    
    for f_job in jobs_to_detail:
        f_job_id = str(f_job['id'])
        job_info = {
            "job_id": f_job_id,
            "incident_ids": extract_incident_string(f_job.get('settings', {})),  # <--- UPDATED HERE
            "version": get_setting(f_job, 'VERSION'),
            "arch": get_setting(f_job, 'ARCH'),
            "error_text": "Error details not found"
        }
        
        try:
            details_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{f_job_id}/details"
            details_resp = requests.get(details_url, verify=False, timeout=API_TIMEOUT)
            details_resp.raise_for_status()
            testresults = details_resp.json().get('job', {}).get('testresults', [])
            
            extracted_texts = []
            for mod in testresults:
                if mod.get('name') == failing_module and mod.get('result') in ['fail', 'failed', 'died']:
                    if mod.get('text_data'):
                        extracted_texts.append(extract_top_10(mod.get('text_data')))
                    
                    for step in mod.get('details', []):
                        if step.get('result') in ['fail', 'failed', 'died'] and step.get('text_data'):
                            extracted_texts.append(extract_top_10(step.get('text_data')))
            
            if extracted_texts:
                job_info["error_text"] = sanitize_text("\n---\n".join(extracted_texts))
                
        except Exception as e:
            job_info["error_text"] = f"Failed fetching details: {str(e)}"
            
        detailed_failing_jobs.append(job_info)

    report = {
        "failing_jobs_with_specified_TEST": {
            "total_module_failures_in_last_150": len(matching_fails),
            "failure_distribution": {
                "by_incident": dict(fail_incidents),
                "by_version": dict(fail_versions),
                "by_arch": dict(fail_arches)
            }
        },
        "passing_jobs_with_specified_TEST": {
            # UPDATED HERE ALSO
            "total_passes_in_last_50": len(passed_jobs_raw),
            "pass_distribution": {
                "by_incident": dict(Counter([extract_incident_string(j.get('settings', {})) for j in passed_jobs_raw])),
                "by_version": dict(Counter([get_setting(j, 'VERSION') for j in passed_jobs_raw])),
                "by_arch": dict(Counter([get_setting(j, 'ARCH') for j in passed_jobs_raw]))
            }
        },
        "detailed_failing_jobs": detailed_failing_jobs
    }

    if current_incident and current_incident.lower() != 'none':
        report["failing_jobs_with_specified_TEST+INCIDENT"] = dict(Counter([f"{get_setting(j, 'ARCH')} + {get_setting(j, 'VERSION')}" for j in inc_failed])) if inc_failed else "No failing jobs were found for this TEST and incident"
        report["passing_jobs_with_specified_TEST+INCIDENT"] = dict(Counter([f"{get_setting(j, 'ARCH')} + {get_setting(j, 'VERSION')}" for j in inc_passed])) if inc_passed else "No passing jobs were found for this TEST and incident"

    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find similar openQA failures.")
    parser.add_argument("--job", required=True, help="Current failing job ID")
    args = parser.parse_args()
    
    cache_path = os.path.join("temp_data", f"{args.job}.json")
    if not os.path.exists(cache_path):
        print(json.dumps({"error": f"Cache file not found for job {args.job}. Run fetch_job_data.py first."}))
        sys.exit(1)
        
    with open(cache_path, "r") as f:
        job_info = json.load(f)
        
    incident_ids = job_info.get("incident_ids", [])
    current_incident = str(incident_ids[0]) if len(incident_ids) == 1 else "None"
    
    print(json.dumps(analyze_similar_failures(
        job_id=args.job,
        failing_module=job_info.get("failing_module"),
        test_name=job_info.get("test"),
        current_incident=current_incident,
        current_version=job_info.get("version", "Unknown"),
        current_arch=job_info.get("arch", "Unknown")
    ), indent=2))