import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import requests
import re
import os
from collections import deque
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def extract_perl_function(code, start_index):
    """
    Extracts a full Perl subroutine by counting curly braces, 
    ignoring braces inside single or double quotes.
    """
    brace_count = 0
    found_first_brace = False
    end_index = -1
    
    in_single_quote = False
    in_double_quote = False
    escape_next = False
    
    for i in range(start_index, len(code)):
        char = code[i]
        
        # Handle escape characters
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        # Toggle between quotes
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
            
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
            
        # Ignore braces when inside a string
        if in_single_quote or in_double_quote:
            continue
            
        if char == '{':
            brace_count += 1
            found_first_brace = True
        elif char == '}':
            brace_count -= 1
            
        # Inside the function block, when the count returns to 0, it closed - found the end
        if found_first_brace and brace_count == 0:
            end_index = i + 1
            break
            
    if end_index != -1:
        return code[start_index:end_index].strip()
        
    # Fallback: if braces were mismatched, grab until the next 'sub' or eof
    lines = code[start_index:].split('\n')
    func_lines = []
    for j, line in enumerate(lines):
        if j > 0 and re.match(r'^[ \t]*sub\s+', line):
            break
        func_lines.append(line)
    return '\n'.join(func_lines).strip()

def trace_functions(test_git_hash, failing_file_path, function_names):
    if not test_git_hash or not failing_file_path or not function_names:
        return {"error": "test_git_hash, failing_file_path, and at least one function name are required."}

    headers = get_github_headers()

    # fetch the repository tree
    tree_url = f"https://api.github.com/repos/os-autoinst/os-autoinst-distri-opensuse/git/trees/{test_git_hash}?recursive=1"
    try:
        resp = requests.get(tree_url, headers=headers, timeout=10)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            return {"error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN environment variable."}
        resp.raise_for_status()
        tree_data = resp.json().get("tree", [])
    except Exception as e:
        return {"error": f"Failed to fetch repo tree: {str(e)}"}

    # Build file lookup map
    file_map = {}
    for item in tree_data:
        path = item["path"]
        if path.endswith(".pm"):
            filename = path.split('/')[-1]
            if filename not in file_map:
                file_map[filename] = []
            file_map[filename].append(path)

    # Regexes and Queue for Recursive Search
    ignore_list = {
        'strict', 'warnings', 'utf8', 'vars', 'constant', 'feature', 
        'testapi', 'basetest', 'mmapi', 'lockapi', 'serial_terminal', 'cv'
    }
    
    import_regex = re.compile(r'^\s*(?:use|require)\s+(?:base\s+|parent\s+)?(?:qw/|qw\()?["\']?([a-zA-Z0-9_:]+)["\']?', re.MULTILINE)
    
    functions_to_find = set(function_names)
    func_regexes = {
        func: re.compile(r'^[ \t]*sub\s+' + re.escape(func) + r'\b', re.MULTILINE)
        for func in functions_to_find
    }

    # BFS Initialization
    queue = deque([failing_file_path])
    visited = [] 
    visited_set = {failing_file_path}
    results = {}
    
    MAX_FILES_TO_SCAN = 50 

    # Queue proccessing
    while queue and functions_to_find:
        if len(visited) >= MAX_FILES_TO_SCAN:
            results["warnings"] = f"Search aborted: reached maximum limit of {MAX_FILES_TO_SCAN} scanned files. Some dependencies might be missing."
            break
            
        current_path = queue.popleft()
        visited.append(current_path)
        
        raw_url = f"https://raw.githubusercontent.com/os-autoinst/os-autoinst-distri-opensuse/{test_git_hash}/{current_path}"
        try:
            cand_resp = requests.get(raw_url, headers=headers, timeout=10)
            if cand_resp.status_code != 200:
                continue
            code = cand_resp.text
        except Exception:
            continue
            
        # Check if any missing functions are in this file
        found_funcs = []
        for func in list(functions_to_find):
            match = func_regexes[func].search(code)
            if match:
                lines_before_match = code[:match.start()].split('\n')
                line_num = len(lines_before_match)
                
                # EXTRACT FULL FUNC BODY
                full_function_body = extract_perl_function(code, match.start())
                
                results[func] = {
                    "file_path": current_path,
                    "line_number": line_num,
                    "snippet": full_function_body
                }
                found_funcs.append(func)
                
        for f in found_funcs:
            functions_to_find.remove(f)

        if not functions_to_find:
            break
            
        # Parse imports and add unseen files to the queue
        for module_name in import_regex.findall(code):
            if module_name in ignore_list:
                continue
            
            target_file = module_name.split('::')[-1] + '.pm'
            if target_file in file_map:
                for resolved_path in file_map[target_file]:
                    if resolved_path not in visited_set:
                        visited_set.add(resolved_path)
                        queue.append(resolved_path)

    if functions_to_find and "warnings" not in results:
        results["warnings"] = f"Could not locate definitions for: {', '.join(functions_to_find)}."
    
    results["_meta"] = {
        "files_scanned_count": len(visited),
        "scanned_paths": visited
    }
        
    return results

def fetch_raw_code(test_git_hash, file_path, _retried=False):
    """Fetches the entire raw code of a file directly from GitHub."""
    headers = get_github_headers()
    repo_name = "os-autoinst/os-autoinst-distri-opensuse"
    raw_url = f"https://raw.githubusercontent.com/{repo_name}/{test_git_hash}/{file_path}"
    
    try:
        resp = requests.get(raw_url, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        return {
            "file_path": file_path,
            "raw_code": resp.text
        }
    except Exception as e:
        error_str = str(e)
        
        # If 404 and the fallback method hasn't ran
        if "404 Client Error" in error_str and not _retried:
            # get substring (after last '/', up to the first '.')
            filename = file_path.rsplit('/', 1)[-1]
            our_substring = filename.split('.')[0]

            # search master for this substring
            search_url = "https://api.github.com/search/code"
            query = f"{our_substring} repo:{repo_name}"
            
            search_headers = headers.copy()
            search_headers["Accept"] = "application/vnd.github.v3.text-match+json"
            
            try:
                search_resp = requests.get(search_url, params={"q": query}, headers=search_headers, timeout=10)
                search_resp.raise_for_status()
                search_data = search_resp.json()
                
                # Regex for: loadtest('<a_string>', name => '<our_substring>'
                # maybe it's dynamically loaded with a different name
                regex_pattern = rf"loadtest\(\s*['\"]([^'\"]+)['\"]\s*,\s*name\s*=>\s*['\"]{re.escape(our_substring)}['\"]"
                new_file_prefix = None
                
                # 3. Parse the results to find regex match
                for item in search_data.get("items", []):
                    for match in item.get("text_matches", []):
                        fragment = match.get("fragment", "")
                        for line in fragment.splitlines():
                            if our_substring in line:
                                re_match = re.search(regex_pattern, line)
                                if re_match:
                                    new_file_prefix = re_match.group(1).rsplit('/', 1)[-1]
                                    break
                        if new_file_prefix: break
                    if new_file_prefix: break
                
                # If found, reconstruct path and retry
                if new_file_prefix:
                    parts = file_path.rsplit('/', 1)
                    if len(parts) == 2:
                        new_file_path = f"{parts[0]}/{new_file_prefix}.pm"
                    else:
                        new_file_path = f"{new_file_prefix}.pm"
                    
                    # Call recursively with the new path, setting _retried to True
                    return fetch_raw_code(test_git_hash, new_file_path, _retried=True)
                    
            except Exception as search_err:
                pass

        # Default fallback
        return {
            "error": f"Failed to fetch raw code from GitHub: {error_str}",
            "attempted_url": raw_url
        }

def main():
    # Show usage if not enough arguments are provided
    if len(sys.argv) < 3:
        usage = (
            "Usage:\n"
            "  Fetch raw code:  python trace_functions.py --raw <job_id>\n"
            "  Trace functions: python trace_functions.py <job_id> <func1> [func2...]"
        )
        print(json.dumps({"error": usage}), flush=True)
        sys.exit(1)
        
    job_id = sys.argv[2] if sys.argv[1] == "--raw" else sys.argv[1]
    cache_path = os.path.join("temp_data", f"{job_id}.json")
    
    if not os.path.exists(cache_path):
        print(json.dumps({"error": f"Cache file not found for job {job_id}. Run fetch_job_data.py first."}))
        sys.exit(1)
        
    with open(cache_path, "r") as f:
        job_info = json.load(f)
        
    git_hash = job_info.get("test_git_hash")
    failing_path = job_info.get("failing_code_context", {}).get("file_path")
        
    # If the user wants to fetch the Raw Code
    if sys.argv[1] == "--raw":
        results = fetch_raw_code(git_hash, failing_path)
        print(json.dumps(results, indent=2), flush=True)
        sys.exit(0)
        
    # If the user wants to trace functions
    functions = sys.argv[2:]
    results = trace_functions(git_hash, failing_path, functions)
    print(json.dumps(results, indent=2), flush=True)

if __name__ == "__main__":
    main()