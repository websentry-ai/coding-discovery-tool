#!/bin/bash
set -euo pipefail

# =============================================================================
# Unbound Scheduled Run Setup (macOS + Linux)
# =============================================================================
#
# Sets up a daily (09:00) background job that re-runs either:
#   - unbound discover  (default — backward-compat with `unbound discover schedule`)
#   - unbound onboard   (when --command onboard, used by `unbound onboard --set-cron`)
#
# Usage:
#   # Discover scheduled scan (back-compat invocation)
#   ./setup-scheduled-scan.sh --api-key <key> --domain <url>
#
#   # Discover via new --command flag
#   ./setup-scheduled-scan.sh --command discover --api-key <key> --domain <url>
#
#   # Onboard scheduled run
#   ./setup-scheduled-scan.sh --command onboard --api-key <key> --discovery-key <key> [--domain <url>]
#
#   # Uninstall
#   ./setup-scheduled-scan.sh --uninstall
#
# Security:
#   - macOS: credentials in Keychain
#   - Linux: credentials in ~/.unbound/scheduled-creds.json (mode 0600)
#
# =============================================================================

LABEL="ai.getunbound.scheduled"
INSTALL_DIR="$HOME/.local/share/unbound"
WRAPPER_SCRIPT="$INSTALL_DIR/run-scheduled.sh"
KEYCHAIN_SERVICE="ai.getunbound.scheduled"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
CREDS_FILE_LINUX="$HOME/.unbound/scheduled-creds.json"

# Linux scheduling: systemd --user timer with Persistent=true so missed runs
# (system off at 09:00) trigger on the next boot/login. Falls back to crontab
# if systemd is not available.
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SYSTEMD_SERVICE_NAME="unbound-scheduled.service"
SYSTEMD_TIMER_NAME="unbound-scheduled.timer"
CRON_MARKER_TAG="# ai.getunbound.scheduled"
CRON_TIME="0 9 * * *" # daily at 09:00 (cron fallback only)

LOG_DIR_MACOS="$HOME/Library/Logs/unbound"
LOG_DIR_LINUX="$INSTALL_DIR/logs"

SCAN_SCRIPT_URL="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"

# -----------------------------------------------------------------------------
# OS detection
# -----------------------------------------------------------------------------
OS=""
case "$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
    *)      echo "Error: Unsupported OS. This script supports macOS and Linux. For Windows, use setup-scheduled-scan.ps1."; exit 1 ;;
esac

LOG_DIR="$LOG_DIR_MACOS"
[ "$OS" = "linux" ] && LOG_DIR="$LOG_DIR_LINUX"

usage() {
    echo "Unbound Scheduled Run Setup"
    echo ""
    echo "Usage:"
    echo "  Install (discover):  $0 [--command discover] --api-key <key> --domain <url>"
    echo "  Install (onboard):   $0 --command onboard --api-key <key> --discovery-key <key> [--domain <url>]"
    echo "  Uninstall:           $0 --uninstall"
    echo ""
    echo "Options:"
    echo "  --command <name>     Subcommand to schedule: 'discover' (default) or 'onboard'"
    echo "  --api-key <key>      User API key (or discovery key when --command discover)"
    echo "  --discovery-key <k>  Discovery key (required for --command onboard)"
    echo "  --domain <url>       Backend URL (e.g., https://backend.getunbound.ai)"
    echo "  --uninstall          Remove the scheduled job"
    echo "  --help               Show this help message"
    exit 1
}

# =============================================================================
# Credential storage — macOS Keychain
# =============================================================================

store_credentials_macos() {
    echo "Storing credentials in macOS Keychain..."
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "command"       >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key"       >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "discovery_key" >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "domain"        >/dev/null 2>&1 || true

    security add-generic-password -s "$KEYCHAIN_SERVICE" -a "command" -w "$COMMAND" -U 2>/dev/null \
        || { echo "Error: Failed to store command in Keychain"; exit 1; }
    security add-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" -w "$API_KEY" -U 2>/dev/null \
        || { echo "Error: Failed to store API key in Keychain"; exit 1; }
    if [ -n "$DISCOVERY_KEY" ]; then
        security add-generic-password -s "$KEYCHAIN_SERVICE" -a "discovery_key" -w "$DISCOVERY_KEY" -U 2>/dev/null \
            || { echo "Error: Failed to store discovery key in Keychain"; exit 1; }
    fi
    if [ -n "$DOMAIN" ]; then
        security add-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" -w "$DOMAIN" -U 2>/dev/null \
            || { echo "Error: Failed to store domain in Keychain"; exit 1; }
    fi
    echo "  Credentials stored in Keychain"
}

remove_credentials_macos() {
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "command"       >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key"       >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "discovery_key" >/dev/null 2>&1 || true
    security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "domain"        >/dev/null 2>&1 || true
    echo "  Removed credentials from Keychain"
}

# =============================================================================
# Credential storage — Linux file (mode 0600)
# =============================================================================

store_credentials_linux() {
    echo "Storing credentials in $CREDS_FILE_LINUX (mode 0600)..."
    # Escape JSON special chars (\, ") in each value. Run dir-creation +
    # file-write in a subshell with umask 077 so both the parent directory
    # and the file get tight permissions (dir 0700, file 0600), and the
    # umask change doesn't leak into subsequent file creation (wrapper, logs).
    json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
    local cmd_e key_e disc_e dom_e
    cmd_e=$(json_escape "$COMMAND")
    key_e=$(json_escape "$API_KEY")
    disc_e=$(json_escape "${DISCOVERY_KEY:-}")
    dom_e=$(json_escape "${DOMAIN:-}")
    (
        umask 077
        mkdir -p "$(dirname "$CREDS_FILE_LINUX")"
        cat > "$CREDS_FILE_LINUX" <<EOF
{
  "command": "$cmd_e",
  "api_key": "$key_e",
  "discovery_key": "$disc_e",
  "domain": "$dom_e"
}
EOF
    )
    chmod 600 "$CREDS_FILE_LINUX"
    echo "  Credentials stored"
}

remove_credentials_linux() {
    if [ -f "$CREDS_FILE_LINUX" ]; then
        rm -f "$CREDS_FILE_LINUX"
        echo "  Removed credentials file"
    fi
}

# =============================================================================
# Wrapper script — runs the scheduled command using stored credentials
# =============================================================================
# Kept on disk after execution (EDR-safe — no download-execute-delete pattern).
# =============================================================================

create_wrapper_script() {
    echo "Creating local wrapper script..."
    mkdir -p "$INSTALL_DIR" "$LOG_DIR"

    cat > "$WRAPPER_SCRIPT" <<WRAPPER_EOF
#!/bin/bash
# Unbound Scheduled Wrapper — runs daily via launchd (macOS) / cron (Linux)
set -euo pipefail

# cron and systemd --user both invoke this with a minimal PATH that excludes
# the dirs npm-global / nvm / homebrew / pipx use, so 'unbound' and 'curl'
# would not be found. Prepend the common user-binary dirs so the onboard
# branch below can locate the CLI regardless of which scheduler triggered us.
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH"

OS=""
case "\$(uname -s)" in
    Darwin) OS="macos" ;;
    Linux)  OS="linux" ;;
esac

LOG_DIR="$LOG_DIR"
mkdir -p "\$LOG_DIR"

log() {
    echo "[\$(date '+%Y-%m-%d %H:%M:%S')] \$1" >> "\$LOG_DIR/scheduled.log"
}

log "=== Starting Unbound scheduled run ==="

# -----------------------------------------------------------------------------
# Retrieve credentials
# -----------------------------------------------------------------------------
COMMAND=""
API_KEY=""
DISCOVERY_KEY=""
DOMAIN=""

if [ "\$OS" = "macos" ]; then
    COMMAND=\$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "command"       -w 2>/dev/null || echo "")
    API_KEY=\$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key"       -w 2>/dev/null || echo "")
    DISCOVERY_KEY=\$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "discovery_key" -w 2>/dev/null || echo "")
    DOMAIN=\$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "domain"        -w 2>/dev/null || echo "")
else
    CREDS_FILE="$CREDS_FILE_LINUX"
    if [ ! -f "\$CREDS_FILE" ]; then
        log "ERROR: Credentials file not found at \$CREDS_FILE"
        exit 1
    fi
    # Minimal JSON value extractor — avoids jq dependency.
    # Matches "field": "value" pairs, tolerating empty strings.
    extract_field() {
        local field="\$1"
        grep -E "\"\$field\"[[:space:]]*:" "\$CREDS_FILE" \
            | head -1 \
            | sed -E 's/.*"'"\$field"'"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/'
    }
    COMMAND=\$(extract_field command)
    API_KEY=\$(extract_field api_key)
    DISCOVERY_KEY=\$(extract_field discovery_key)
    DOMAIN=\$(extract_field domain)
fi

if [ -z "\$COMMAND" ] || [ -z "\$API_KEY" ]; then
    log "ERROR: Required credentials missing (command='\$COMMAND', api_key set: \$([ -n "\$API_KEY" ] && echo yes || echo no))"
    exit 1
fi

# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------
case "\$COMMAND" in
    discover)
        # Backward-compat path: download install.sh and run it.
        if [ -z "\$DOMAIN" ]; then
            log "ERROR: domain missing from stored credentials (required for discover)"
            exit 1
        fi
        SCRIPT_PATH="$INSTALL_DIR/install.sh"
        log "Downloading install script to: \$SCRIPT_PATH"
        if ! curl -fsSL -o "\$SCRIPT_PATH" "$SCAN_SCRIPT_URL"; then
            log "ERROR: Failed to download install script"
            exit 1
        fi
        if [ ! -s "\$SCRIPT_PATH" ] || ! head -1 "\$SCRIPT_PATH" | grep -q '^#!/'; then
            log "ERROR: Downloaded script invalid"
            exit 1
        fi
        chmod +x "\$SCRIPT_PATH"
        log "Executing: \$SCRIPT_PATH --api-key *** --domain \$DOMAIN"
        "\$SCRIPT_PATH" --api-key "\$API_KEY" --domain "\$DOMAIN" >> "\$LOG_DIR/scheduled.log" 2>&1
        ;;
    onboard)
        # Re-run the full unbound onboard flow daily.
        if [ -z "\$DISCOVERY_KEY" ]; then
            log "ERROR: discovery_key missing from stored credentials (required for onboard)"
            exit 1
        fi
        UNBOUND_BIN=\$(command -v unbound 2>/dev/null || echo "")
        if [ -z "\$UNBOUND_BIN" ]; then
            log "ERROR: 'unbound' CLI not found in PATH. Install with: npm install -g unbound-cli"
            exit 1
        fi
        ARGS=(onboard --api-key "\$API_KEY" --discovery-key "\$DISCOVERY_KEY")
        [ -n "\$DOMAIN" ] && ARGS+=(--domain "\$DOMAIN")
        log "Executing: unbound onboard --api-key *** --discovery-key *** \${DOMAIN:+--domain \$DOMAIN}"
        "\$UNBOUND_BIN" "\${ARGS[@]}" >> "\$LOG_DIR/scheduled.log" 2>&1
        ;;
    *)
        log "ERROR: Unknown command '\$COMMAND'"
        exit 1
        ;;
esac

EXIT_CODE=\$?
if [ \$EXIT_CODE -eq 0 ]; then
    log "Scheduled run completed successfully"
else
    log "Scheduled run failed with exit code: \$EXIT_CODE"
fi
log "=== Finished ==="
WRAPPER_EOF

    chmod +x "$WRAPPER_SCRIPT"
    echo "  Wrapper script created: $WRAPPER_SCRIPT"
}

# =============================================================================
# Scheduler — macOS launchd
# =============================================================================

install_macos() {
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

    # Unload existing job if present
    if launchctl list "$LABEL" &>/dev/null; then
        echo "Removing previous scheduled job..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
            || launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi

    store_credentials_macos
    create_wrapper_script

    echo "Creating LaunchAgent plist..."
    cat > "$PLIST_PATH" <<EOF
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
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scheduled.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scheduled.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF
    echo "  Plist created: $PLIST_PATH"

    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || launchctl load "$PLIST_PATH"
    echo ""
    echo "Unbound scheduled run set up."
    echo "  Command:     $COMMAND"
    echo "  Schedule:    Daily at 09:00 (runs on install + at each login via RunAtLoad)"
    echo "  Logs:        ${LOG_DIR}/scheduled.log"
    echo "  Uninstall:   unbound discover unschedule"
}

# =============================================================================
# Scheduler — Linux (systemd --user timer preferred, crontab fallback)
# =============================================================================
# Why systemd over plain cron: systemd timers support Persistent=true which
# triggers a missed unit on the next boot/login. Plain crontab silently skips
# any run whose scheduled time fell while the machine was off — bad for laptops.
# We fall back to crontab only when systemd isn't available.
# =============================================================================

install_linux() {
    mkdir -p "$LOG_DIR" "$INSTALL_DIR"

    store_credentials_linux
    create_wrapper_script

    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        install_linux_systemd
    else
        echo "  systemd not available — falling back to crontab (no catch-up for missed runs)"
        install_linux_crontab
    fi

    echo ""
    echo "Unbound scheduled run set up."
    echo "  Command:     $COMMAND"
    echo "  Schedule:    Daily at 09:00 (systemd: catches up missed runs; crontab: no catch-up)"
    echo "  Logs:        ${LOG_DIR}/scheduled.log"
    echo "  Uninstall:   unbound discover unschedule"
}

install_linux_systemd() {
    mkdir -p "$SYSTEMD_USER_DIR"

    # Disable + stop any existing timer before rewriting unit files so the daemon
    # picks up the new content on reload.
    systemctl --user disable --now "$SYSTEMD_TIMER_NAME" >/dev/null 2>&1 || true

    # systemd --user services inherit a minimal PATH that does not include
    # ~/.local/bin, /usr/local/bin, or homebrew paths where the `unbound` CLI
    # or `curl` may live. Set PATH explicitly so the onboard wrapper can find
    # the binary at run time.
    cat > "$SYSTEMD_USER_DIR/$SYSTEMD_SERVICE_NAME" <<EOF
[Unit]
Description=Unbound scheduled run

[Service]
Type=oneshot
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStart=$WRAPPER_SCRIPT
EOF

    cat > "$SYSTEMD_USER_DIR/$SYSTEMD_TIMER_NAME" <<EOF
[Unit]
Description=Unbound scheduled run (daily at 09:00, catches up missed runs)

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
Unit=$SYSTEMD_SERVICE_NAME

[Install]
WantedBy=timers.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "$SYSTEMD_TIMER_NAME"

    echo "  systemd --user timer installed: $SYSTEMD_USER_DIR/$SYSTEMD_TIMER_NAME"
    echo "  Note: if you log out, run \`loginctl enable-linger \$USER\` to keep the timer alive."
}

install_linux_crontab() {
    local cron_line="$CRON_TIME $WRAPPER_SCRIPT $CRON_MARKER_TAG"
    local existing filtered
    existing=$(crontab -l 2>/dev/null || true)
    filtered=$(printf '%s\n' "$existing" | grep -vF "$CRON_MARKER_TAG" || true)
    {
        if [ -n "$filtered" ]; then printf '%s\n' "$filtered"; fi
        printf '%s\n' "$cron_line"
    } | crontab -
    echo "  Crontab entry installed (no catch-up for missed runs)"
}

# =============================================================================
# Uninstall
# =============================================================================

uninstall() {
    echo "Uninstalling Unbound scheduled run..."
    if [ "$OS" = "macos" ]; then
        if launchctl list "$LABEL" &>/dev/null; then
            launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
                || launchctl unload "$PLIST_PATH" 2>/dev/null || true
            echo "  Stopped LaunchAgent"
        fi
        [ -f "$PLIST_PATH" ] && rm "$PLIST_PATH" && echo "  Removed plist"
        remove_credentials_macos
    else
        # Tear down both systemd and crontab paths — whichever was installed.
        if command -v systemctl >/dev/null 2>&1; then
            systemctl --user disable --now "$SYSTEMD_TIMER_NAME" >/dev/null 2>&1 || true
            rm -f "$SYSTEMD_USER_DIR/$SYSTEMD_TIMER_NAME" "$SYSTEMD_USER_DIR/$SYSTEMD_SERVICE_NAME"
            systemctl --user daemon-reload >/dev/null 2>&1 || true
            echo "  Removed systemd timer/service"
        fi
        local existing
        existing=$(crontab -l 2>/dev/null || true)
        if printf '%s\n' "$existing" | grep -qF "$CRON_MARKER_TAG"; then
            printf '%s\n' "$existing" | grep -vF "$CRON_MARKER_TAG" | crontab -
            echo "  Removed cron entry"
        fi
        remove_credentials_linux
    fi
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

COMMAND="discover" # default for back-compat with `unbound discover schedule`
API_KEY=""
DISCOVERY_KEY=""
DOMAIN=""
DO_UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --command)
            if [ $# -lt 2 ]; then echo "Error: --command requires a value"; usage; fi
            COMMAND="$2"; shift 2 ;;
        --api-key)
            if [ $# -lt 2 ]; then echo "Error: --api-key requires a value"; usage; fi
            API_KEY="$2"; shift 2 ;;
        --discovery-key)
            if [ $# -lt 2 ]; then echo "Error: --discovery-key requires a value"; usage; fi
            DISCOVERY_KEY="$2"; shift 2 ;;
        --domain)
            if [ $# -lt 2 ]; then echo "Error: --domain requires a value"; usage; fi
            DOMAIN="$2"; shift 2 ;;
        --uninstall) DO_UNINSTALL=true; shift ;;
        --help|-h) usage ;;
        *) echo "Error: Unknown option '$1'"; usage ;;
    esac
done

if [ "$DO_UNINSTALL" = true ]; then
    uninstall
fi

# Validation
if [ "$COMMAND" != "discover" ] && [ "$COMMAND" != "onboard" ]; then
    echo "Error: --command must be 'discover' or 'onboard' (got '$COMMAND')"
    usage
fi
if [ -z "$API_KEY" ]; then
    echo "Error: --api-key is required"
    usage
fi
if [ "$COMMAND" = "onboard" ] && [ -z "$DISCOVERY_KEY" ]; then
    echo "Error: --discovery-key is required when --command onboard"
    usage
fi
if [ "$COMMAND" = "discover" ] && [ -z "$DOMAIN" ]; then
    echo "Error: --domain is required when --command discover"
    usage
fi

# Dispatch
if [ "$OS" = "macos" ]; then
    install_macos
else
    install_linux
fi
