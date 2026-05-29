#!/bin/bash

# Define paths
GEMINI_DIR="$HOME/.gemini"
USER_POLICY_DIR="$GEMINI_DIR/policies"
USER_POLICY_FILE="$USER_POLICY_DIR/policy.toml"
BACKUP_POLICY_FILE="$USER_POLICY_DIR/policy.toml.backup"
MARKER_DIR_CREATED="$USER_POLICY_DIR/.policies_dir_created"
MARKER_SKILL_ACTIVE="$USER_POLICY_DIR/.skill_policy_active"
SKILL_POLICY_FILE="./.gemini/policies/policy.toml"

# 1. Foolproof Check: The home .gemini folder must exist
if [ ! -d "$GEMINI_DIR" ]; then
    echo "Error: The directory $GEMINI_DIR does not exist."
    echo "Please ensure the Gemini CLI is installed and initialized before running this script."
    exit 1
fi

# Determine if we are activating or restoring based on the active marker
if [ -f "$MARKER_SKILL_ACTIVE" ]; then
    # ==========================================
    # RESTORE ORIGINAL STATE
    # ==========================================
    echo "Restoring original Gemini CLI policy..."

    # Remove the strict skill policy
    if [ -f "$USER_POLICY_FILE" ]; then
        rm "$USER_POLICY_FILE"
    fi

    # Restore the user's original policy if a backup exists
    if [ -f "$BACKUP_POLICY_FILE" ]; then
        mv "$BACKUP_POLICY_FILE" "$USER_POLICY_FILE"
        echo "Original policy restored successfully."
    else
        echo "No original policy found to restore. Skill policy removed."
    fi

    # Clean up the active marker
    rm -f "$MARKER_SKILL_ACTIVE"

    # If this script created the policies directory, attempt to remove it
    if [ -f "$MARKER_DIR_CREATED" ]; then
        rm -f "$MARKER_DIR_CREATED"
        # rmdir will only succeed if the directory is completely empty
        rmdir "$USER_POLICY_DIR" 2>/dev/null
        if [ ! -d "$USER_POLICY_DIR" ]; then
            echo "Cleaned up the temporary policies directory."
        fi
    fi

    echo "Restore complete."

else
    # ACTIVATE SKILL POLICY
    echo "Activating strict openQA Triage policy..."

    # Verify the project policy file exists locally
    if [ ! -f "$SKILL_POLICY_FILE" ]; then
        echo "Error: Skill policy file not found at $SKILL_POLICY_FILE."
        echo "Please ensure you are running this script from the project root."
        exit 1
    fi

    # Create the policies directory if it does not exist
    if [ ! -d "$USER_POLICY_DIR" ]; then
        mkdir -p "$USER_POLICY_DIR"
        touch "$MARKER_DIR_CREATED"
    fi

    # Backup the existing user policy if one exists
    if [ -f "$USER_POLICY_FILE" ]; then
        mv "$USER_POLICY_FILE" "$BACKUP_POLICY_FILE"
        echo "Original policy backed up to $BACKUP_POLICY_FILE"
    fi

    # Copy the skill policy into place
    if cp "$SKILL_POLICY_FILE" "$USER_POLICY_FILE"; then
        touch "$MARKER_SKILL_ACTIVE"
        echo "Strict skill policy is now active."
        echo "Agent is restricted to local scripts."
    else
        echo "Error: Failed to copy the skill policy."
        # Attempt rollback if the copy failed for whateverreasons
        if [ -f "$BACKUP_POLICY_FILE" ]; then
            mv "$BACKUP_POLICY_FILE" "$USER_POLICY_FILE"
        fi
        exit 1
    fi
fi