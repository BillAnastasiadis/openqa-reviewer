import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import argparse
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENQA_BASE_URL = "https://openqa.suse.de"
SMELT_GRAPHQL_ENDPOINT = "https://smelt.suse.de/graphql"

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
    return set(incident_ids)

def get_job_data(job_id):
    """Returns a tuple of (incident_ids_set, version_string)"""
    if not job_id or str(job_id).lower() == 'none':
        return set(), "Unknown"
    api_url = f"{OPENQA_BASE_URL}/api/v1/jobs/{job_id}"
    try:
        resp = requests.get(api_url, verify=False, timeout=30)
        resp.raise_for_status()
        settings = resp.json().get('job', {}).get('settings', {})
        return extract_incident_ids(settings), settings.get('VERSION', 'Unknown')
    except Exception:
        return set(), "Unknown"

def fetch_packages_for_incident(incident_id, token=None):
    query = f"""
        query getIncidentPackages {{
          incidents(incidentId: {incident_id}) {{
            edges {{ node {{ packages {{ edges {{ node {{ name }} }} }} }} }}
          }}
        }}
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(SMELT_GRAPHQL_ENDPOINT, params={"query": query}, headers=headers, timeout=20, verify=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [f"API_ERROR: {str(e)}"]

    package_names = set()
    edges = data.get("data", {}).get("incidents", {}).get("edges", []) or []
    for edge in edges:
        pkg_edges = edge.get("node", {}).get("packages", {}).get("edges", []) or []
        for pkg_edge in pkg_edges:
            name = pkg_edge.get("node", {}).get("name")
            if name:
                package_names.add(name)
    return sorted(list(package_names))

def main():
    parser = argparse.ArgumentParser(description="Isolate culprit incidents in aggregate tests.")
    parser.add_argument("--current", required=True, help="Current failing job ID")
    parser.add_argument("--failures", nargs='*', default=[], help="Historical failing job IDs with the exact same error")
    args = parser.parse_args()
    
    current_incidents, current_version = get_job_data(args.current)
    if not current_incidents:
        print(json.dumps({"error": "No incident IDs found in current job. This is not an aggregate update run."}))
        sys.exit(0)

    # Track two separate intersections
    suspects_all = current_incidents.copy()
    suspects_same_version = current_incidents.copy()
    same_version_failures = []

    # Intersect with historical failures
    for f_id in args.failures:
        f_inc, f_version = get_job_data(f_id)
        
        suspects_all = suspects_all.intersection(f_inc)
        
        # Only intersect the second list if the version matches the current job
        if f_version == current_version:
            same_version_failures.append(f_id)
            suspects_same_version = suspects_same_version.intersection(f_inc)

    last_good = "None"
    cache_path = os.path.join("temp_data", f"{args.current}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached_data = json.load(f)
            last_good_data = cached_data.get("last_good_test")
            if isinstance(last_good_data, dict):
                last_good = str(last_good_data.get("job_id", "None"))
        except Exception:
            pass

    # Subtract last known good incidents from both lists
    if last_good and last_good.lower() != 'none':
        good_inc, _ = get_job_data(last_good)
        suspects_all = suspects_all - good_inc
        suspects_same_version = suspects_same_version - good_inc

    smelt_token = os.getenv("SMELT_TOKEN")
    
    # Only hit SMELT once per unique incident across both sets
    all_unique_suspects = suspects_all.union(suspects_same_version)
    fetched_packages = {}
    for inc_id in all_unique_suspects:
        fetched_packages[str(inc_id)] = fetch_packages_for_incident(inc_id, token=smelt_token)

    results_all = {str(inc_id): fetched_packages[str(inc_id)] for inc_id in suspects_all}
    results_same_version = {str(inc_id): fetched_packages[str(inc_id)] for inc_id in suspects_same_version}

    likely_culprit = None
    if len(results_all) == 1:
        likely_culprit = {
            "intersection_used": "all_versions", 
            "incident": results_all
        }
    elif len(results_same_version) == 1:
        likely_culprit = {
            "intersection_used": "same_version", 
            "incident": results_same_version
        }
    elif len(results_same_version) > 1:
        likely_culprit = "Multiple candidates remain. Please review the packages in 'same_version_intersection' and identify which one matches the error trace."
    else:
        likely_culprit = "No surviving incidents. Likely NOT an update regression."

    output = {
        "likely_culprit": likely_culprit,
        "all_versions_intersection": {
            "surviving_incidents": results_all
        },
        "same_version_intersection": {
            "surviving_incidents": results_same_version
        },
        "metadata": {
            "current_job": args.current,
            "current_version": current_version,
            "last_good_job_subtracted": last_good,
            "historical_failures_intersected_all": args.failures,
            "historical_failures_intersected_same_version": same_version_failures,
            "surviving_incident_count_all": len(results_all),
            "surviving_incident_count_same_version": len(results_same_version)
        }
    }
    
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()