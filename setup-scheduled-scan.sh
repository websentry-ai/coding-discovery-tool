#!/bin/bash
set -euo pipefail

# =============================================================================
# Unbound AI Tools Discovery - Scheduled Scan Setup (macOS)
# =============================================================================
#
# This script sets up a LaunchAgent to run the AI tools discovery scan
# every 12 hours on macOS.
#
# Usage:
#   Install:   ./setup-scheduled-scan.sh --api-key <key> --domain <url>
#   Uninstall: ./setup-scheduled-scan.sh --uninstall
#
# Security Features:
#   - Credentials stored in macOS Keychain (not in plist)
#   - No remote code execution patterns in LaunchAgent plist
#   - Download-to-file pattern for auto-updates (EDR-safe)
#
# =============================================================================

LABEL="ai.getunbound.discovery"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/unbound"
INSTALL_DIR="$HOME/.local/share/unbound"
WRAPPER_SCRIPT="$INSTALL_DIR/run-discovery.sh"
KEYCHAIN_SERVICE="ai.getunbound.discovery"

# Remote URL (used by wrapper script, NOT by plist)
SCAN_SCRIPT_URL="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"

INTERVAL=43200 # 12 hours in seconds

usage() {
    echo "Unbound AI Tools Discovery - Scheduled Scan Setup"
    echo ""
    echo "Usage:"
    echo "  Install:   $0 --api-key <key> --domain <url>"
    echo "  Uninstall: $0 --uninstall"
    echo ""
    echo "Options:"
    echo "  --api-key <key>   Your Unbound API key"
    echo "  --domain <url>    Your Unbound domain (e.g., https://app.getunbound.ai)"
    echo "  --uninstall       Remove the scheduled scan"
    echo "  --help            Show this help message"
    exit 1
}

# =============================================================================
# Keychain Functions - Secure Credential Storage
# =============================================================================

store_credentials_in_keychain() {
    local api_key="$1"
    local domain="$2"

    echo "Storing credentials in macOS Keychain..."

    # Remove existing entries if present (suppress all output)
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" >/dev/null 2>&1 || true

    # Store new credentials with error checking
    if ! security add-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" -w "$api_key" -U 2>/dev/null; then
        echo "Error: Failed to store API key in Keychain"
        echo "  You may need to grant Keychain access or unlock your Keychain"
        exit 1
    fi

    if ! security add-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" -w "$domain" -U 2>/dev/null; then
        echo "Error: Failed to store domain in Keychain"
        echo "  You may need to grant Keychain access or unlock your Keychain"
        exit 1
    fi

    echo "  Credentials stored securely in Keychain"
}

remove_credentials_from_keychain() {
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" >/dev/null 2>&1 || true
    echo "  Removed credentials from Keychain"
}

# =============================================================================
# Wrapper Script - EDR-Safe Auto-Update Pattern
# =============================================================================
# Downloads install script to a permanent location, validates it, then executes.
# File is kept after execution (not deleted) to avoid anti-forensics pattern
# that EDR tools like CrowdStrike flag as malware staging behavior.
# =============================================================================

create_wrapper_script() {
    echo "Creating local wrapper script..."

    mkdir -p "$INSTALL_DIR"

    cat > "$WRAPPER_SCRIPT" << 'WRAPPER_EOF'
#!/bin/bash
# =============================================================================
# Unbound Discovery Wrapper Script
# =============================================================================
# Executed by LaunchAgent every 12 hours.
# Downloads and runs the latest discovery script using EDR-safe patterns.
#
# Security: File is kept after execution to avoid "download-execute-delete"
# pattern that EDR tools flag as malware staging (anti-forensics behavior).
# =============================================================================

set -euo pipefail

KEYCHAIN_SERVICE="ai.getunbound.discovery"
LOG_DIR="$HOME/Library/Logs/unbound"
INSTALL_DIR="$HOME/.local/share/unbound"
INSTALL_SCRIPT_URL="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"

# Permanent location for downloaded script (not temp - EDR safe)
SCRIPT_PATH="$INSTALL_DIR/install.sh"

mkdir -p "$LOG_DIR"
mkdir -p "$INSTALL_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_DIR/scan.log"
}

log "=== Starting Unbound Discovery ==="

# Step 1: Retrieve credentials from Keychain
API_KEY=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" -w 2>/dev/null) || {
    log "ERROR: Could not retrieve API key from Keychain"
    exit 1
}

DOMAIN=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" -w 2>/dev/null) || {
    log "ERROR: Could not retrieve domain from Keychain"
    exit 1
}

log "Credentials retrieved from Keychain"

# Step 2: Download to permanent location (overwrites previous version)
log "Downloading install script to: $SCRIPT_PATH"

if ! curl -fsSL -o "$SCRIPT_PATH" "$INSTALL_SCRIPT_URL"; then
    log "ERROR: Failed to download install script"
    exit 1
fi

# Step 3: Validate the downloaded file
if [ ! -s "$SCRIPT_PATH" ]; then
    log "ERROR: Downloaded script is empty"
    exit 1
fi

if ! head -1 "$SCRIPT_PATH" | grep -q '^#!/'; then
    log "ERROR: Downloaded file is not a valid script"
    exit 1
fi

log "Download validated successfully"

# Step 4: Execute the local script
chmod +x "$SCRIPT_PATH"

log "Executing local script..."
"$SCRIPT_PATH" --api-key "$API_KEY" --domain "$DOMAIN" >> "$LOG_DIR/scan.log" 2>&1
EXIT_CODE=$?

# Note: Script is intentionally NOT deleted after execution.
# Keeping the file avoids the "download-execute-delete" pattern
# that EDR tools like CrowdStrike flag as malware staging.

if [ $EXIT_CODE -eq 0 ]; then
    log "Discovery completed successfully"
else
    log "Discovery failed with exit code: $EXIT_CODE"
fi

log "=== Finished ==="
WRAPPER_EOF

    chmod +x "$WRAPPER_SCRIPT"
    echo "  Wrapper script created: $WRAPPER_SCRIPT"
}

# =============================================================================
# LaunchAgent Plist - Clean Configuration
# =============================================================================
# The plist only references a local script.
# No curl, no credentials, no remote URLs.
# =============================================================================

create_plist() {
    echo "Creating LaunchAgent plist..."

    mkdir -p "$(dirname "$PLIST_PATH")"

    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${WRAPPER_SCRIPT}</string>
    </array>
    <key>StartInterval</key>
    <integer>${INTERVAL}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scan.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scan.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

    echo "  Plist created: $PLIST_PATH"
}

# =============================================================================
# Uninstall - Complete Cleanup
# =============================================================================

uninstall() {
    echo "Uninstalling Unbound scheduled scan..."

    # Stop LaunchAgent
    if launchctl list "$LABEL" &>/dev/null; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
        echo "  Stopped scheduled job"
    fi

    # Remove plist
    if [ -f "$PLIST_PATH" ]; then
        rm "$PLIST_PATH"
        echo "  Removed plist"
    fi

    # Remove credentials from Keychain
    remove_credentials_from_keychain

    # Remove wrapper script and install directory
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        echo "  Removed install directory"
    fi

    echo "Done."
    exit 0
}

# =============================================================================
# Main
# =============================================================================

# macOS check
if [ "$(uname -s)" != "Darwin" ]; then
    echo "Error: This script is for macOS only (uses launchd)."
    exit 1
fi

API_KEY=""
DOMAIN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-key)
            if [ $# -lt 2 ]; then echo "Error: --api-key requires a value"; usage; fi
            API_KEY="$2"; shift 2 ;;
        --domain)
            if [ $# -lt 2 ]; then echo "Error: --domain requires a value"; usage; fi
            DOMAIN="$2"; shift 2 ;;
        --uninstall) uninstall ;;
        --help|-h) usage ;;
        *) echo "Error: Unknown option '$1'"; usage ;;
    esac
done

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    usage
fi

# Create log directory
mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

# Unload existing job if present
if launchctl list "$LABEL" &>/dev/null; then
    echo "Removing previous scheduled job..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# Install
store_credentials_in_keychain "$API_KEY" "$DOMAIN"
create_wrapper_script
create_plist

# Load the job
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || launchctl load "$PLIST_PATH"

echo ""
echo "Unbound scan scheduled successfully."
echo "  Schedule:    Every 12 hours (runs immediately on install)"
echo "  Logs:        ${LOG_DIR}/scan.log"
echo "  Errors:      ${LOG_DIR}/scan.err"
echo "  Credentials: Stored in macOS Keychain (not in plist)"
echo "  Uninstall:   $0 --uninstall"
echo ""
