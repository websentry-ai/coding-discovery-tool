#!/bin/bash
set -euo pipefail

LABEL="ai.getunbound.discovery"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/unbound"
SCAN_SCRIPT="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"
INTERVAL=43200 # 12 hours in seconds

usage() {
    echo "Usage: $0 --api-key <key> --domain <url>"
    echo "       $0 --uninstall"
    exit 1
}

uninstall() {
    echo "Uninstalling Unbound scheduled scan..."
    if launchctl list "$LABEL" &>/dev/null; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
        echo "  Stopped scheduled job."
    fi
    if [ -f "$PLIST_PATH" ]; then
        rm "$PLIST_PATH"
        echo "  Removed $PLIST_PATH"
    fi
    echo "Done."
    exit 0
}

API_KEY=""
DOMAIN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-key)  API_KEY="$2"; shift 2 ;;
        --domain)   DOMAIN="$2"; shift 2 ;;
        --uninstall) uninstall ;;
        *) usage ;;
    esac
done

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    usage
fi

# Create log directory
mkdir -p "$LOG_DIR"

# Unload existing job if present
if launchctl list "$LABEL" &>/dev/null; then
    echo "Removing previous scheduled job..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# Create the launchd plist
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>curl -fsSL ${SCAN_SCRIPT} | bash -s -- --api-key ${API_KEY} --domain ${DOMAIN}</string>
    </array>
    <key>StartInterval</key>
    <integer>${INTERVAL}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scan.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scan.err</string>
</dict>
</plist>
EOF

# Load the job
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || launchctl load "$PLIST_PATH"

echo ""
echo "Unbound scan scheduled successfully."
echo "  Schedule:  Every 12 hours (runs immediately on install)"
echo "  Logs:      ${LOG_DIR}/scan.log"
echo "  Errors:    ${LOG_DIR}/scan.err"
echo "  Uninstall: $0 --uninstall"
echo ""
