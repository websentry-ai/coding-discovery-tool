################################################################################
# AI Tools Discovery - Installation and Execution Script (Windows PowerShell)
# 
# Windows-compatible script that downloads and runs the coding discovery tool.
#
# Usage:
#   $script = Invoke-WebRequest -Uri https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.ps1 -UseBasicParsing; Invoke-Expression $script.Content --api-key YOUR_API_KEY --domain YOUR_DOMAIN
#   OR
#   .\install.ps1 --api-key YOUR_API_KEY --domain YOUR_DOMAIN
################################################################################

param(
    [Parameter(Mandatory=$false)]
    [string]$ApiKey,
    
    [Parameter(Mandatory=$false)]
    [string]$Domain
)

# Exit on any error
$ErrorActionPreference = "Stop"

# ==============================================================================
# CONFIGURATION
# ==============================================================================

$REPO_URL = "https://github.com/websentry-ai/coding-discovery-tool.git"
$BRANCH = "main"
$TEMP_DIR = Join-Path $env:TEMP "coding-discovery-tool-$(New-Guid)"

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

function Write-Info {
    param([string]$Message)
    Write-Host "ℹ $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

# Cleanup temporary directory on exit
function Cleanup {
    if (Test-Path $TEMP_DIR) {
        Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Register cleanup on exit
Register-EngineEvent PowerShell.Exiting -Action { Cleanup } | Out-Null

# ==============================================================================
# DEPENDENCY CHECKS
# ==============================================================================

function Test-Python {
    # Check for python3 first
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return "python3"
    }
    
    # Check for python
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        # Verify it's Python 3
        $version = & python --version 2>&1
        if ($version -match "Python 3") {
            return "python"
        } else {
            Write-Error "Python 3 is required but only Python 2 was found."
            Write-Info "Please install Python 3 and try again."
            exit 1
        }
    }
    
    Write-Error "Python 3 is required but not found."
    Write-Info "Please install Python 3 and try again."
    exit 1
}

# ==============================================================================
# REPOSITORY DOWNLOAD
# ==============================================================================

function Download-Repo {
    # Check if git is available
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        Write-Error "Git is not installed."
        Write-Info "This script requires git to download the full package structure."
        Write-Info "Please install git and try again, or clone the repository manually."
        exit 1
    }
    
    # Create temporary directory
    New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
    
    # Clone repository
    try {
        # Try sparse checkout first (more efficient)
        & git clone --depth 1 --branch $BRANCH --filter=blob:none --sparse $REPO_URL $TEMP_DIR 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Push-Location $TEMP_DIR
            & git sparse-checkout set scripts/ 2>&1 | Out-Null
            Pop-Location
        } else {
            # Fallback to full clone if sparse checkout fails
            & git clone --depth 1 --branch $BRANCH $REPO_URL $TEMP_DIR 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Git clone failed"
            }
        }
    } catch {
        Write-Error "Failed to clone repository: $_"
        exit 1
    }
}

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

function Main {
    # Check dependencies
    $pythonCmd = Test-Python
    Download-Repo
    
    # Change to repository root directory
    Push-Location $TEMP_DIR
    
    try {
        # Build argument list
        $argsList = @()
        if ($ApiKey) {
            $argsList += "--api-key"
            $argsList += $ApiKey
        }
        if ($Domain) {
            $argsList += "--domain"
            $argsList += $Domain
        }
        
        # Execute the discovery script
        & $pythonCmd -m scripts.coding_discovery_tools.ai_tools_discovery $argsList
    } finally {
        Pop-Location
    }
}

# ==============================================================================
# ARGUMENT PARSING AND SCRIPT ENTRY POINT
# ==============================================================================

# Check if script was invoked with arguments (for Invoke-Expression method)
if ($args.Count -gt 0) {
    # Parse arguments manually (for Invoke-Expression method)
    for ($i = 0; $i -lt $args.Count; $i++) {
        $arg = $args[$i]
        
        # Handle --api-key or -ApiKey or --ApiKey formats
        if (($arg -eq "--api-key" -or $arg -eq "-ApiKey" -or $arg -eq "--ApiKey") -and $i + 1 -lt $args.Count) {
            $ApiKey = $args[$i + 1]
            $i++
        }
        # Handle --domain or -Domain or --Domain formats
        elseif (($arg -eq "--domain" -or $arg -eq "-Domain" -or $arg -eq "--Domain") -and $i + 1 -lt $args.Count) {
            $Domain = $args[$i + 1]
            $i++
        }
    }
}

# Check if required arguments are provided
if (-not $ApiKey -or -not $Domain) {
    Write-Error "Missing required arguments"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\install.ps1 --api-key YOUR_API_KEY --domain YOUR_DOMAIN"
    Write-Host ""
    Write-Host "Or run via PowerShell:"
    Write-Host "  `$script = Invoke-WebRequest -Uri https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.ps1 -UseBasicParsing; Invoke-Expression `$script.Content --api-key YOUR_API_KEY --domain YOUR_DOMAIN"
    Write-Host ""
    exit 1
}

# Execute main function
Main

