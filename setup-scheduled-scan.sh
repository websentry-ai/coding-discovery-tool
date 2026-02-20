#!/bin/bash
set -euo pipefail

LABEL="ai.getunbound.discovery"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/unbound"
SCAN_SCRIPT="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"
SELF_URL="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/setup-scheduled-scan.sh"
INTERVAL=43200 # 12 hours in seconds

usage() {
    echo "Usage:"
    echo "  Install:   curl -fsSL $SELF_URL | bash -s -- --api-key <key> --domain <url>"
    echo "  Uninstall: curl -fsSL $SELF_URL | bash -s -- --uninstall"
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
        --help) usage ;;
        *) echo "Error: Unknown option '$1'"; usage ;;
    esac
done

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    usage
fi

# Escape XML special characters in user-provided values
xml_escape() {
    local s="$1"
    s="${s//&/&amp;}"
    s="${s//</&lt;}"
    s="${s//>/&gt;}"
    s="${s//\"/&quot;}"
    s="${s//\'/&apos;}"
    echo "$s"
}

SAFE_API_KEY=$(xml_escape "$API_KEY")
SAFE_DOMAIN=$(xml_escape "$DOMAIN")

# Create log directory
mkdir -p "$LOG_DIR"
mkdir -p "$HOME/Library/LaunchAgents"

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
        <string>curl -fsSL ${SCAN_SCRIPT} | bash -s -- --api-key ${SAFE_API_KEY} --domain ${SAFE_DOMAIN}</string>
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
echo "  Uninstall: curl -fsSL $SELF_URL | bash -s -- --uninstall"
echo ""
