#!/bin/bash
################################################################################
# AI Tools Discovery - Installation and Execution Script
# 
# Cross-platform script that downloads and runs the coding discovery tool.
# Can be executed directly or via curl pipe (similar to Cursor installation).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh | bash -s -- --api-key YOUR_API_KEY --domain YOUR_DOMAIN --app_name APP_NAME
#   OR
#   ./install.sh --api-key YOUR_API_KEY --domain YOUR_DOMAIN --app_name APP_NAME
################################################################################

set -e  # Exit on any error

# ==============================================================================
# CONFIGURATION
# ==============================================================================

REPO_URL="https://github.com/websentry-ai/coding-discovery-tool.git"
BRANCH="main"
TEMP_DIR=$(mktemp -d 2>/dev/null || mktemp -d -t 'coding-discovery-tool')

# ==============================================================================
# OUTPUT COLORS
# ==============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Color

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

# Cleanup temporary directory on exit
cleanup() {
    if [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}
trap cleanup EXIT

# Print colored messages
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

# ==============================================================================
# SYSTEM DETECTION
# ==============================================================================

# Detect the operating system
detect_os() {
    case "$(uname -s)" in
        Darwin*)
            echo "Darwin"
            ;;
        Linux*)
            echo "Linux"
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo "Windows"
            ;;
        *)
            echo "Unknown"
            ;;
    esac
}

# ==============================================================================
# DEPENDENCY CHECKS
# ==============================================================================

# Check if Python 3 is installed and available
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        # Check if 'python' command is actually Python 3
        if python --version 2>&1 | grep -q "Python 3"; then
            PYTHON_CMD="python"
        else
            print_error "Python 3 is required but only Python 2 was found."
            print_info "Please install Python 3 and try again."
            exit 1
        fi
    else
        print_error "Python 3 is required but not found."
        print_info "Please install Python 3 and try again."
        exit 1
    fi
}

# ==============================================================================
# REPOSITORY DOWNLOAD
# ==============================================================================

check_git_functional() {
    # Check if git command exists
    if ! command -v git &> /dev/null; then
        return 1
    fi

    # Test if git actually works (catches Xcode CLT stub on fresh macOS)
    local git_output
    git_output=$(git --version 2>&1)

    # Check for Xcode CLT error messages
    if echo "$git_output" | grep -q "xcrun: error\|xcode-select\|command line tools"; then
        return 1
    fi

    if ! echo "$git_output" | grep -q "git version"; then
        return 1
    fi

    return 0
}

download_with_git() {
    local clone_output

    if clone_output=$(git clone --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO_URL" "$TEMP_DIR" 2>&1); then
        cd "$TEMP_DIR"
        git sparse-checkout set scripts/ 2>/dev/null || true
        return 0
    fi

    if echo "$clone_output" | grep -q "xcrun: error\|xcode-select\|invalid active developer path"; then
        return 1
    fi

    if git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$TEMP_DIR" 2>&1; then
        return 0
    fi

    return 1
}

download_with_curl() {
    local ARCHIVE_URL="https://github.com/websentry-ai/coding-discovery-tool/archive/refs/heads/${BRANCH}.tar.gz"

    if ! command -v curl &> /dev/null; then
        print_error "curl is not installed."
        print_info "Please install curl and try again."
        return 1
    fi

    if ! command -v tar &> /dev/null; then
        print_error "tar is not installed."
        print_info "Please install tar and try again."
        return 1
    fi

    if curl -fsSL "$ARCHIVE_URL" 2>/dev/null | tar -xz -C "$TEMP_DIR" --strip-components=1 2>/dev/null; then
        return 0
    fi

    return 1
}

download_repo() {
    print_info "Downloading repository..."

    if check_git_functional; then
        if download_with_git; then
            print_success "Repository downloaded successfully (via git)"
            return 0
        fi
        print_warning "Git download failed, trying fallback method..."
    fi

    if download_with_curl; then
        print_success "Repository downloaded successfully (via curl)"
        return 0
    fi

    print_error "Failed to download repository."
    echo ""
    print_info "Please check your internet connection and try again."

    if [ "$(detect_os)" = "Darwin" ]; then
        echo ""
        print_info "Both Git and Curl methods failed. Please check these installations and try again."
    fi

    exit 1
}

# ==============================================================================
# MDM SUPPORT FUNCTIONS (macOS + root only)
# ==============================================================================

SYSTEM_CONFIG_DIR="/Library/Application Support/Unbound"
SYSTEM_CONFIG_FILE="$SYSTEM_CONFIG_DIR/config"
GLOBAL_INSTALL_DIR="/usr/local/share/unbound"
GLOBAL_WRAPPER_SCRIPT="$GLOBAL_INSTALL_DIR/run-discovery.sh"

create_system_config() {
    local api_key="${1//$'\n'/}"
    local domain="${2//$'\n'/}"

    print_info "Creating system config..."
    mkdir -p "$SYSTEM_CONFIG_DIR"

    printf 'API_KEY=%s\nDOMAIN=%s\n' "$api_key" "$domain" > "$SYSTEM_CONFIG_FILE"

    # 0640 root:staff — readable by logged-in users, not world-readable
    chown root:staff "$SYSTEM_CONFIG_FILE"
    chmod 0640 "$SYSTEM_CONFIG_FILE"

    print_success "System config created: $SYSTEM_CONFIG_FILE"
}

create_wrapper_script() {
    print_info "Creating global wrapper script..."

    mkdir -p "$GLOBAL_INSTALL_DIR"

    cat > "$GLOBAL_WRAPPER_SCRIPT" << 'WRAPPER_EOF'
#!/bin/bash
# Unbound Discovery Wrapper (Global / MDM)
# Executed by LaunchAgent in each user session.
# File is kept after execution (not deleted) to avoid EDR alerts.

set -euo pipefail

KEYCHAIN_SERVICE="ai.getunbound.discovery"
LOG_DIR="$HOME/Library/Logs/unbound"
LOCAL_DIR="$HOME/.local/share/unbound"
INSTALL_SCRIPT_URL="https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh"

SCRIPT_PATH="$LOCAL_DIR/install.sh"

mkdir -p "$LOG_DIR" 2>/dev/null || true
mkdir -p "$LOCAL_DIR" || { log "ERROR: Cannot create $LOCAL_DIR"; exit 1; }

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_DIR/scan.log"
}

log "=== Starting Unbound Discovery ==="

API_KEY=""
DOMAIN=""

# Credential chain: managed preferences > system config > user keychain
API_KEY=$(defaults read ai.getunbound.discovery api_key 2>/dev/null) || true
DOMAIN=$(defaults read ai.getunbound.discovery domain 2>/dev/null) || true

if [ -n "$API_KEY" ] && [ -n "$DOMAIN" ]; then
    log "Credentials retrieved from managed preferences"
fi

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    SYSTEM_CONFIG="/Library/Application Support/Unbound/config"
    if [ -f "$SYSTEM_CONFIG" ]; then
        if [ -z "$API_KEY" ]; then
            API_KEY=$(grep '^API_KEY=' "$SYSTEM_CONFIG" | head -1 | cut -d= -f2-)
        fi
        if [ -z "$DOMAIN" ]; then
            DOMAIN=$(grep '^DOMAIN=' "$SYSTEM_CONFIG" | head -1 | cut -d= -f2-)
        fi
        if [ -n "$API_KEY" ] && [ -n "$DOMAIN" ]; then
            log "Credentials retrieved from system config"
        fi
    fi
fi

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    if [ -z "$API_KEY" ]; then
        API_KEY=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "api_key" -w 2>/dev/null) || true
    fi
    if [ -z "$DOMAIN" ]; then
        DOMAIN=$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "domain" -w 2>/dev/null) || true
    fi
    if [ -n "$API_KEY" ] && [ -n "$DOMAIN" ]; then
        log "Credentials retrieved from user keychain"
    fi
fi

if [ -z "$API_KEY" ] || [ -z "$DOMAIN" ]; then
    log "ERROR: No credentials found"
    exit 1
fi

log "Downloading install script to: $SCRIPT_PATH"

if ! curl -fsSL -o "$SCRIPT_PATH" "$INSTALL_SCRIPT_URL"; then
    log "ERROR: Failed to download install script"
    exit 1
fi

if [ ! -s "$SCRIPT_PATH" ]; then
    log "ERROR: Downloaded script is empty"
    exit 1
fi

if ! head -1 "$SCRIPT_PATH" | grep -q '^#!/'; then
    log "ERROR: Downloaded file is not a valid script"
    exit 1
fi

log "Download validated successfully"
chmod +x "$SCRIPT_PATH"

log "Executing local script..."
"$SCRIPT_PATH" --api-key "$API_KEY" --domain "$DOMAIN" >> "$LOG_DIR/scan.log" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "Discovery completed successfully"
else
    log "Discovery failed with exit code: $EXIT_CODE"
fi

log "=== Finished ==="
WRAPPER_EOF

    chmod +x "$GLOBAL_WRAPPER_SCRIPT"
    print_success "Wrapper script created: $GLOBAL_WRAPPER_SCRIPT"
}

uninstall_mdm_artifacts() {
    print_info "Removing MDM artifacts..."

    if [ -f "$SYSTEM_CONFIG_FILE" ]; then
        rm -f "$SYSTEM_CONFIG_FILE"
        print_success "Removed system config: $SYSTEM_CONFIG_FILE"
    fi

    rmdir "$SYSTEM_CONFIG_DIR" 2>/dev/null || true

    if [ -f "$GLOBAL_WRAPPER_SCRIPT" ]; then
        rm -f "$GLOBAL_WRAPPER_SCRIPT"
        print_success "Removed wrapper script: $GLOBAL_WRAPPER_SCRIPT"
    fi

    rmdir "$GLOBAL_INSTALL_DIR" 2>/dev/null || true

    print_info "Note: MDM-deployed plist must be removed via your MDM platform"
    print_info "Note: Per-user caches (~/.local/share/unbound/, ~/Library/Logs/unbound/) are left in place"
    print_success "MDM artifacts removed"
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

main() {
    check_python
    download_repo

    cd "$TEMP_DIR"
    $PYTHON_CMD -m scripts.coding_discovery_tools.ai_tools_discovery "$@"

    # Create system-wide MDM support files when running as root on macOS
    if [ "$(detect_os)" = "Darwin" ] && [ "$(id -u)" -eq 0 ]; then
        echo ""
        print_info "Running as root on macOS — setting up MDM support files..."

        local mdm_api_key=""
        local mdm_domain=""
        local args=("$@")
        local i=0
        while [ $i -lt ${#args[@]} ]; do
            case "${args[$i]}" in
                --api-key)
                    if [ $((i + 1)) -ge ${#args[@]} ]; then
                        print_warning "Missing value for --api-key; skipping MDM file creation"
                        break
                    fi
                    mdm_api_key="${args[$((i+1))]}"
                    i=$((i + 2))
                    ;;
                --domain)
                    if [ $((i + 1)) -ge ${#args[@]} ]; then
                        print_warning "Missing value for --domain; skipping MDM file creation"
                        break
                    fi
                    mdm_domain="${args[$((i+1))]}"
                    i=$((i + 2))
                    ;;
                *)
                    i=$((i + 1))
                    ;;
            esac
        done

        if [ -n "$mdm_api_key" ] && [ -n "$mdm_domain" ]; then
            create_system_config "$mdm_api_key" "$mdm_domain"
            create_wrapper_script
            echo ""
            print_success "MDM support files created. Deploy the LaunchAgent plist via your MDM platform."
            print_info "  Config:  $SYSTEM_CONFIG_FILE"
            print_info "  Wrapper: $GLOBAL_WRAPPER_SCRIPT"
            print_info "  Plist:   See mdm/ai.getunbound.discovery.plist in the repository"
        else
            print_warning "Could not extract --api-key and --domain; skipping MDM file creation"
        fi
    fi
}

# ==============================================================================
# ARGUMENT PARSING AND SCRIPT ENTRY POINT
# ==============================================================================

for arg in "$@"; do
    if [ "$arg" = "--uninstall" ]; then
        if [ "$(detect_os)" != "Darwin" ]; then
            print_error "--uninstall is only supported on macOS"
            exit 1
        fi
        if [ "$(id -u)" -ne 0 ]; then
            print_error "--uninstall must be run as root (sudo)"
            exit 1
        fi
        uninstall_mdm_artifacts
        exit 0
    fi
done

if [ $# -eq 0 ]; then
    echo ""
    print_error "Missing required arguments"
    echo ""
    echo "Usage:"
    echo "  $0 --api-key YOUR_API_KEY --domain YOUR_DOMAIN [--app_name APP_NAME]"
    echo "  $0 --uninstall  (macOS, root only — remove MDM artifacts)"
    echo ""
    echo "Or run via curl:"
    echo "  curl -fsSL https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/$BRANCH/install.sh | bash -s -- --api-key YOUR_API_KEY --domain YOUR_DOMAIN --app_name APP_NAME"
    echo ""
    exit 1
fi

main "$@"
