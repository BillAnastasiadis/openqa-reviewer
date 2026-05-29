#!/bin/bash

# Ensure the script runs from the actual project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Track process IDs and path markers
BROKER_PID=""
MARKER_SKILL_ACTIVE="$HOME/.gemini/policies/.skill_policy_active"

# AUTOMATIC CLEANUP FUNCTION
cleanup() {
    echo -e "\n\nReview session ended. Cleaning up environment..."
    
    # Terminate the background host broker daemon safely
    if [ -n "$BROKER_PID" ] && kill -0 "$BROKER_PID" 2>/dev/null; then
        echo "Stopping Host Broker Daemon (PID: $BROKER_PID)..."
        kill "$BROKER_PID"
        wait "$BROKER_PID" 2>/dev/null
    fi

    # Deactivate the strict policy and restore your original policy file
    if [ -f "./toggle_policy.sh" ]; then
        if [ -f "$MARKER_SKILL_ACTIVE" ]; then
            echo "Deactivating triage policy..."
            bash ./toggle_policy.sh > /dev/null
            echo "Original Gemini CLI policy file restored successfully."
        fi
    fi

    # Purge any leftover temporary bridge pipes
    rm -rf bridge/requests/* bridge/responses/* 2>/dev/null
    
    echo "System fully restored. Goodbye!"
}

# The Bash Trap intercepts ANY exit condition (typing exit, closing tab, or Ctrl+C)
# and guarantees that the cleanup function runs no matter what.
trap cleanup EXIT INT TERM

# INITIALIZATION & ENVIROMENT HARMONIZATION

# 1. Activate strict policy file
if [ -f "./toggle_policy.sh" ]; then
    if [ ! -f "$MARKER_SKILL_ACTIVE" ]; then
        bash ./toggle_policy.sh
    fi
else
    echo "Error: toggle_policy.sh not found in the root directory."
    exit 1
fi

# USE `.ENV` FILE CONFIGURATIONS DIRECTLY
if [ -f ".gemini/.env" ]; then
    set -a
    source .gemini/.env
    set +a
elif [ -f "$HOME/.gemini/.env" ]; then
    set -a
    source "$HOME/.gemini/.env"
    set +a
fi

# 3. HIJACK THE DOCKER COMMAND STRING VIA PATH INTERCEPTION
# Forces gemini-cli to seamlessly utilize rootless Podman engine flags
TMP_BIN_DIR=$(mktemp -d)
echo '#!/bin/sh' > "$TMP_BIN_DIR/docker"
echo 'exec podman "$@"' >> "$TMP_BIN_DIR/docker"
chmod +x "$TMP_BIN_DIR/docker"
export PATH="$TMP_BIN_DIR:$PATH"

# NETWORK HARDENING:
# Dynamically extract the active non-VPN physical interface (e.g., wlan0, eth0, etc)
DEFAULT_IFACE=$(ip route show | grep '^default' | awk '{print $5}' | head -n1)

if [ -n "$DEFAULT_IFACE" ]; then
    echo "Network Shield Active: Forcing egress via '$DEFAULT_IFACE' with Public DNS Routing"
    # We combine the slide's routing constraints with a public DNS to prevent the hang
    export SANDBOX_FLAGS="$SANDBOX_FLAGS --network=slirp4netns:outbound_addr=$DEFAULT_IFACE --dns=1.1.1.1"
else
    echo "Warning: Could not detect physical network interface. Falling back to standard isolation."
fi

export SANDBOX_FLAGS="$SANDBOX_FLAGS --userns=keep-id"

# Ensure data-passing conduits exist
mkdir -p bridge/requests bridge/responses

# Stars the Host Broker quietly in the background
echo "Spawning host broker daemon to manage VPN traffic..."
python3 host_broker.py > /dev/null 2>&1 &
BROKER_PID=$!

sleep 0.2

# LAUNCH THE GEMINI AGENT
echo "Initializing openQA Review Assistant Session..."
echo "------------------------------------------------------------"

# Launch interactive mode using the exported .env variables
gemini -s "$@"