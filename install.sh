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

# Download the repository using curl + tar
download_repo() {
    # Use GitHub's archive download
    local ARCHIVE_URL="https://github.com/websentry-ai/coding-discovery-tool/archive/refs/heads/${BRANCH}.tar.gz"

    print_info "Downloading repository..."

    # Check if curl is available
    if ! command -v curl &> /dev/null; then
        print_error "curl is not installed."
        print_info "Please install curl and try again."
        exit 1
    fi

    # Check if tar is available
    if ! command -v tar &> /dev/null; then
        print_error "tar is not installed."
        print_info "Please install tar and try again."
        exit 1
    fi

    # Download and extract the archive
    local download_error
    if download_error=$(curl -fsSL "$ARCHIVE_URL" 2>&1 | tar -xz -C "$TEMP_DIR" --strip-components=1 2>&1); then
        print_success "Repository downloaded successfully"
    else
        print_error "Failed to download repository."
        echo ""
        print_info "Error: $download_error"
        print_info "Please check your internet connection and try again."
        exit 1
    fi
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

main() {
    # Check dependencies (silently)
    check_python
    download_repo
    
    # Change to repository root directory
    cd "$TEMP_DIR"
    
    # Execute the discovery script with all provided arguments
    # (The Python script will handle its own output)
    $PYTHON_CMD -m scripts.coding_discovery_tools.ai_tools_discovery "$@"
}

# ==============================================================================
# ARGUMENT PARSING AND SCRIPT ENTRY POINT
# ==============================================================================

# Check if arguments were provided
if [ $# -eq 0 ]; then
    echo ""
    print_error "Missing required arguments"
    echo ""
    echo "Usage:"
    echo "  $0 --api-key YOUR_API_KEY --domain YOUR_DOMAIN [--app_name APP_NAME]"
    echo ""
    echo "Or run via curl:"
    echo "  curl -fsSL https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/$BRANCH/install.sh | bash -s -- --api-key YOUR_API_KEY --domain YOUR_DOMAIN --app_name APP_NAME"
    echo ""
    exit 1
fi

# Execute main function with all arguments
main "$@"
