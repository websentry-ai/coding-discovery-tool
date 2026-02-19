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
