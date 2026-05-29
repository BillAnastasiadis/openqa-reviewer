# openQA Review Assistant

The openQA Review Assistant is a Gemini CLI-powered agent designed to automate the triage of SUSE openQA job failures. It fetches logs, traces Perl dependencies, analyzes recent GitHub commits, and cross-references historical job data to diagnose test flakes, infrastructure issues, or regressions.

To prevent the LLM from executing arbitrary commands on your host machine, this tool operates within a strict security boundary. It relies on a curated set of local Python scripts executed through an air-gapped host broker (`host_broker.py`).

> **Important Note:** The tool respects the settings found inside the user's `settings.json`. If the user hasn't configured a sandbox, the agent will run on the host directly. Despite the agent being aggressively restrained by the enforced policy file, it is far from good practice to run any agent directly on the host machine.

## Prerequisites

* **Gemini CLI:** Installed and authenticated.
* **Python 3.8+**
* **Python Packages:** The scripts require `requests`, `urllib3`, `pyenchant`, and the `openqa_log_local` module.
* **GitHub Personal Access Token:** Required to fetch raw code, trace functions, and parse commit histories.

> **Note on `pyenchant`:** > This package relies on a system-level C library (`libenchant`) to function. Depending on your OS, you may need to install it via your system's package manager before running `pip install`:
> * **openSUSE:** `sudo zypper install libenchant-2-2`
> * **Debian/Ubuntu:** `sudo apt install libenchant-2-dev`
> * **macOS:** `brew install enchant`

## Setup Instructions

1. **Install Python Dependencies**
   Ensure you have the required packages installed:
   ```bash
   pip install requests urllib3 pyenchant openqa_log_local
   ```

2. **Configure GitHub Credentials (Required)**
   The agent requires a GitHub Personal Access Token to parse commit histories and trace code paths. You can provide this token using one of two methods:

   **Method A: Environment Variables (Recommended)**
   The safest way to provide your GitHub token is via an environment variable, keeping the secret entirely out of the agent's container. The assistant and Gemini CLI can load variables persistently from a `.env` file. 
   
   You can place your token in a `.env` file located at either `.gemini/.env` or `$HOME/.gemini/.env`:
   ```env
   GITHUB_TOKEN="ghp_your_token_here"
   ```
   Alternatively, you can simply export it directly in your shell before running the assistant:
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   ```

   **Method B: Local Config File (Not Recommended)**
   As a fallback, you can use a local configuration file. *Note: This method is discouraged because the credential file will physically reside inside the agent's container. Although the strict execution policy explicitly prohibits the agent from reading or accessing the `credentials/` directory, the environment variable method is inherently safer.*
   
   Copy the example configuration file:
   ```bash
   cp credentials/creds.conf.example credentials/creds.conf
   ```
   Edit `credentials/creds.conf` and add your token:
   ```env
   GITHUB_TOKEN=ghp_your_token_here
   ```
   *(Note: `creds.conf` is ignored by git to prevent accidental exposure.)*

## Usage

You should always launch the assistant via the provided wrapper script. This script automatically applies the strict execution policy, sets up network sandboxing, and spawns the background host broker daemon.

1. **Start the Session:**
   Run the wrapper script from the root of the project:
   ```bash
   ./run_review.sh
   ```

2. **Invoke the Assistant:**
   Once the interactive Gemini session starts, invoke the skill and provide an openQA job ID:
   ```text
   /review 1234567
   ```
   You can also append context to guide the agent:
   ```text
   /review 1234567 - I suspect this is a regression related to the recent systemd update.
   ```

## Architecture & Security

* **Host Broker (`host_broker.py`):** The LLM runs in a sandbox and communicates via JSON payloads written to a `bridge/` directory. The host broker daemon watches this directory and strictly executes only the Python scripts explicitly allowed in its hardcoded list.
* **Strict Policy (`toggle_policy.sh`):** When you run `run_review.sh`, it temporarily swaps your default Gemini CLI policy with `policy.toml`. This limits the agent's tool access, explicitly blocking arbitrary shell commands, preventing directory browsing outside the required scope, and denying read access to the `credentials/` folder.
* **Automatic Cleanup:** When you exit the Gemini CLI session, the wrapper script traps the exit signal, terminates the host broker, purges the bridge communication files, and safely restores your original Gemini CLI policy.