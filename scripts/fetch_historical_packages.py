import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SMELT_GRAPHQL_ENDPOINT = "https://smelt.suse.de/graphql"

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
        resp = requests.get(SMELT_GRAPHQL_ENDPOINT, params={"query": query}, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
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
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python fetch_historical_packages.py <id1> <id2> ..."}))
        sys.exit(1)
        
    # Grab unique IDs, filter out non-digits, and slice the last 8
    raw_ids = sys.argv[1:]
    incident_ids = list(dict.fromkeys([int(i) for i in raw_ids if i.isdigit()]))[-8:]
    
    smelt_token = os.getenv("SMELT_TOKEN")
    
    results = {}
    for i_id in incident_ids:
        results[str(i_id)] = fetch_packages_for_incident(i_id, token=smelt_token)
        
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()