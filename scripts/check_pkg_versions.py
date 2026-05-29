#!/usr/bin/env python3
import proxy_helper
proxy_helper.handle_sandbox()

import sys
import json
import argparse
import os
import tempfile
import re

original_stdout = sys.stdout
sys.stdout = open(os.devnull, 'w')

try:
    from openqa_log_local import openQA_log_local
except ImportError:
    sys.stdout.close()
    sys.stdout = original_stdout
    print(json.dumps({"error": "The openqa_log_local module is not installed."}))
    sys.exit(1)


def build_pkg_regex(pkg_name):
    """
    Package NEVRA format:
    [Name]-[Digit][Anything]-[Anything].[ValidArchitecture]
    """
    pattern = (
        rf'\b({re.escape(pkg_name)}' 
        r'-\d[a-zA-Z0-9_.~+]*'
        r'-[a-zA-Z0-9_.~+]+'
        r'\.(?:x86_64|noarch|aarch64|s390x|ppc64le|i[3456]86|src|nosrc))\b'
    )
    return re.compile(pattern)

def build_json_pkg_regex(pkg_name):
    """
    Creates a regex to match package metadata in JSON.
    Expected format snippet: "name": "pkg", "version": "v", "release": "r", ... "arch": "a"
    """
    # Using [^}]+ to efficiently skip over unneeded keys (like "epoch": null) 
    # without risking catastrophic backtracking across the whole line.
    pattern = (
        rf'"name"\s*:\s*"{re.escape(pkg_name)}"\s*,\s*'
        r'"version"\s*:\s*"([^"]+)"\s*,\s*'
        r'"release"\s*:\s*"([^"]+)"'
        r'[^}]+?'
        r'"arch"\s*:\s*"([^"]+)"'
    )
    return re.compile(pattern)

def main():
    global original_stdout
    
    try:
        parser = argparse.ArgumentParser(description="Check package versions across openQA incident logs.")
        parser.add_argument("--jobs", nargs='+', required=True, 
                            help="List of openQA job/incident IDs (e.g., 123456 123457)")
        parser.add_argument("--packages", nargs='+', required=True, 
                            help="List of package names to check (e.g., kernel-default systemd)")
        parser.add_argument("--logs", nargs='+', default=['serial_terminal.txt', 'deploy_qesap_ansible-qesap_exec_ansible.log.txt'], 
                            help="Specific log filenames to search. Checked in the exact order provided.")
        args = parser.parse_args()

        try:
            oll = openQA_log_local(host='openqa.suse.de')
        except Exception as e:
            raise RuntimeError(f"Failed to connect to openQA: {str(e)}")

        results = {}
        temp_dir = tempfile.gettempdir()
        pkg_regexes = {pkg: build_pkg_regex(pkg) for pkg in args.packages}
        json_pkg_regexes = {pkg: build_json_pkg_regex(pkg) for pkg in args.packages}

        for job_id in args.jobs:
            results[job_id] = {pkg: "package version not found in logs" for pkg in args.packages}
            packages_to_find = set(args.packages)
            
            for log_filename in args.logs:
                if not packages_to_find:
                    break  # skip remaining logs if all pkgs found
                    
                dest_path = os.path.join(temp_dir, f"{job_id}_{log_filename}")
                
                try:
                    oll.client.download_log_to_file_1(
                        job_id=job_id, 
                        filename=log_filename, 
                        destination_path=dest_path
                    )
                    
                    if os.path.exists(dest_path):
                        found_in_this_log = set()
                        
                        with open(dest_path, 'r', encoding='utf-8', errors='replace') as f:
                            for line in f:
                                for pkg in packages_to_find:
                                    if pkg in line:
                                        match = pkg_regexes[pkg].search(line)
                                        if match:
                                            results[job_id][pkg] = match.group(1)
                                            found_in_this_log.add(pkg)
                                        else:
                                            j_match = json_pkg_regexes[pkg].search(line)
                                            if j_match:
                                                version, release, arch = j_match.groups()
                                                results[job_id][pkg] = f"{pkg}-{version}-{release}.{arch}"
                                                found_in_this_log.add(pkg)
                        
                        packages_to_find -= found_in_this_log
                                
                except Exception:
                    pass
                finally:
                    # Cleanup
                    if os.path.exists(dest_path):
                        try:
                            os.remove(dest_path)
                        except Exception:
                            pass

        sys.stdout.close()
        sys.stdout = original_stdout
        print(json.dumps(results, indent=2))

    except SystemExit:
        sys.stdout.close()
        sys.stdout = original_stdout
        raise
    except Exception as e:
        sys.stdout.close()
        sys.stdout = original_stdout
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()