import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import os

# Set the default repository
REPO = "os-autoinst/os-autoinst-distri-opensuse"
MAX_COMMITS = 40
MAX_PATCH_LENGTH = 3000

def get_github_token():
    """
    Attempts to resolve a GitHub token from the environment,
    falling back to a local credentials/creds.conf file.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
        
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    creds_path = os.path.join(PROJECT_ROOT, "credentials", "creds.conf")
    if os.path.exists(creds_path):
        try:
            with open(creds_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith("GITHUB_TOKEN=") or line.startswith("token="):
                        return line.split("=", 1)[1].strip('"\' ')
                    if line.startswith("ghp_") or line.startswith("github_pat_"):
                        return line
        except Exception as e:
            print(f"DEBUG: Failed to read {creds_path}: {e}", file=sys.stderr)
            
    return None

def get_github_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def get_commit_details(commit_url, headers, target_files=None):
    """
    Fetches the specific file changes for a single commit.
    Truncates large diffs to protect the LLM context window.
    """
    try:
        resp = requests.get(commit_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        files_changed = []
        for f in data.get("files", []):
            filename = f.get("filename")
            
            if target_files and filename not in target_files:
                continue
                
            patch = f.get("patch", "")
            
            if len(patch) > MAX_PATCH_LENGTH:
                patch = patch[:MAX_PATCH_LENGTH] + "\n\n... [PATCH TRUNCATED FOR CONTEXT LIMIT]"
            
            files_changed.append({
                "filename": filename,
                "status": f.get("status"), 
                "patch": patch
            })
        return files_changed
    except Exception as e:
        return [{"error": f"Failed to fetch diff details: {str(e)}"}]

def get_commits_between(base_hash, head_hash, target_files=None):
    """
    Highly optimized commit comparison using Set Intersection.
    Finds the exact commits touching the target files without guessing.
    """
    headers = get_github_headers()
    compare_url = f"https://api.github.com/repos/{REPO}/compare/{base_hash}...{head_hash}"
    
    all_commits_data = []
    
    # Fetch overall comparison
    try:
        resp = requests.get(compare_url, headers=headers, timeout=15)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            return {"error": "GitHub API rate limit exceeded."}
        if resp.status_code == 404:
            return {"error": f"Could not find comparison between {base_hash} and {head_hash}."}
        resp.raise_for_status()
        
        data = resp.json()
        
        # swap hashes if reverse
        if data.get("status") == "behind":
            print("DEBUG: Hashes provided in reverse order. Auto-swapping...", file=sys.stderr)
            base_hash, head_hash = head_hash, base_hash
            compare_url = f"https://api.github.com/repos/{REPO}/compare/{base_hash}...{head_hash}"
            resp = requests.get(compare_url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            # Prevent infinite loop/bad state if branches are entirely divergent
            if data.get("status") == "behind":
                return {"error": "Commit histories are fully divergent; cannot compute a linear diff."}

        all_commits_data.extend(data.get("commits", []))
        
        # move through the rest of the commits
        while "next" in resp.links:
            next_url = resp.links["next"]["url"]
            resp = requests.get(next_url, headers=headers, timeout=15)
            resp.raise_for_status()
            all_commits_data.extend(resp.json().get("commits", []))

    except Exception as e:
        return {"error": f"Failed to fetch repository comparison: {str(e)}"}
        
    shas_in_range = [c.get("sha") for c in all_commits_data]
    commits_metadata = {c.get("sha"): c for c in all_commits_data}
    
    commits_to_fetch = []
    
    if target_files:
        relevant_shas = set()
        for file_path in target_files:
            # Ask gh for the last 100 commits that touched this specific file
            path_url = f"https://api.github.com/repos/{REPO}/commits?sha={head_hash}&path={file_path}&per_page=100"
            try:
                p_resp = requests.get(path_url, headers=headers, timeout=10)
                if p_resp.status_code == 200:
                    file_history = p_resp.json()
                    for fc in file_history:
                        relevant_shas.add(fc.get("sha"))
            except Exception as e:
                print(f"DEBUG: Path query failed: {e}", file=sys.stderr)
                
        # Intersect the file's history with our specific base-to-head range
        commits_to_fetch = [sha for sha in shas_in_range if sha in relevant_shas]
    else:
        commits_to_fetch = shas_in_range
        
    total_relevant = len(commits_to_fetch)
    warning = None
    
    if total_relevant > MAX_COMMITS:
        half_max = MAX_COMMITS // 2
        commits_to_fetch = commits_to_fetch[:half_max] + commits_to_fetch[-half_max:]
        warning = f"Found {total_relevant} relevant commits touching the targets. Truncated to oldest {half_max} and newest {half_max}."
        
    processed_commits = []
    
    for sha in commits_to_fetch:
        metadata = commits_metadata.get(sha, {})
        commit_url = metadata.get("url") or f"https://api.github.com/repos/{REPO}/commits/{sha}"
        is_merge_commit = len(metadata.get("parents", [])) > 1
        
        files_changed = get_commit_details(commit_url, headers, target_files)
        
        if target_files and not files_changed:
            continue
            
        if not files_changed and is_merge_commit:
            continue
            
        commit_info = {
            "sha": sha,
            "author": metadata.get("commit", {}).get("author", {}).get("name", "Unknown"),
            "message": metadata.get("commit", {}).get("message", "").strip(),
            "files_changed": files_changed
        }
        processed_commits.append(commit_info)
        
    results = {
        "base_commit": base_hash,
        "head_commit": head_hash,
        "target_files_filtered": target_files,
        "total_commits_in_range": len(shas_in_range),
        "relevant_commits_found": total_relevant,
        "commits_returned": len(processed_commits),
        "commits": processed_commits
    }
    
    if warning:
        results["warning"] = warning
        
    return results

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python compare_commits.py <job_id>"}), flush=True)
        sys.exit(1)
        
    job_id = sys.argv[1]
    cache_path = os.path.join("temp_data", f"{job_id}.json")
    
    if not os.path.exists(cache_path):
        print(json.dumps({"error": f"Cache file not found for job {job_id}. Run fetch_job_data.py first."}))
        sys.exit(1)
        
    with open(cache_path, "r") as f:
        job_info = json.load(f)
        
    head_hash = job_info.get("test_git_hash")
    base_hash = job_info.get("last_good_test", {}).get("test_git_hash")
    target_files = job_info.get("failing_code_context", {}).get("relevant_file_paths")
    
    if not base_hash or not head_hash:
        print(json.dumps({"error": "Could not determine base or head git hashes from cached job data."}))
        sys.exit(1)
    
    results = get_commits_between(base_hash, head_hash, target_files)
    print(json.dumps(results, indent=2), flush=True)
if __name__ == "__main__":
    main()