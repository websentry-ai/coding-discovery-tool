<#
.SYNOPSIS
    AI Tools Discovery - Installation and Execution Script for Windows

.DESCRIPTION
    Cross-platform script that downloads and runs the coding discovery tool.
    Can be executed directly or via web download (similar to Cursor installation).

.PARAMETER ApiKey
    Required. Your API key for authentication.

.PARAMETER Domain
    Required. The domain/backend URL.

.PARAMETER AppName
    Optional. The application name.

.EXAMPLE
    # Download and run:
    Invoke-WebRequest https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.ps1 -OutFile install.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -ApiKey "YOUR_API_KEY" -Domain "YOUR_DOMAIN"

.EXAMPLE
    # Run directly:
    .\install.ps1 -ApiKey "YOUR_API_KEY" -Domain "YOUR_DOMAIN" -AppName "APP_NAME"
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$ApiKey,

    [Parameter(Mandatory=$false)]
    [string]$Domain,

    [Parameter(Mandatory=$false)]
    [string]$AppName
)

$REPO_URL = "https://github.com/websentry-ai/coding-discovery-tool.git"
$BRANCH = "main"
$TEMP_DIR = Join-Path $env:TEMP "coding-discovery-tool-$(Get-Random)"

function Write-Info {
    param([string]$Message)
    Write-Host "i " -ForegroundColor Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[!] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-ErrorMessage {
    param([string]$Message)
    Write-Host "[X] " -ForegroundColor Red -NoNewline
    Write-Host $Message
}

function Remove-TempDirectory {
    if (Test-Path $TEMP_DIR) {
        Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Register cleanup on script exit
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Remove-TempDirectory }

function Get-PythonCommand {
    # Try python3 first
    try {
        $version = & python3 --version 2>&1
        if ($version -match "Python 3") {
            return "python3"
        }
    } catch {}

    # Try python
    try {
        $version = & python --version 2>&1
        if ($version -match "Python 3") {
            return "python"
        }
    } catch {}

    # Try py launcher (Windows Python Launcher)
    try {
        $version = & py -3 --version 2>&1
        if ($version -match "Python 3") {
            return "py -3"
        }
    } catch {}

    return $null
}

function Test-GitInstalled {
    try {
        $null = & git --version 2>&1
        return $true
    } catch {
        return $false
    }
}

function Get-Repository {
    if (Test-GitInstalled) {
        # Create temp directory
        New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null

        # Try sparse checkout first (more efficient)
        try {
            & git clone --depth 1 --branch $BRANCH --filter=blob:none --sparse $REPO_URL $TEMP_DIR 2>&1 | Out-Null
            Push-Location $TEMP_DIR
            & git sparse-checkout set scripts/ 2>&1 | Out-Null
            Pop-Location
            return $true
        } catch {
            # Fallback to full clone
            try {
                Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
                & git clone --depth 1 --branch $BRANCH $REPO_URL $TEMP_DIR 2>&1 | Out-Null
                return $true
            } catch {
                return $false
            }
        }
    } else {
        Write-ErrorMessage "Git is not installed."
        Write-Info "This script requires git to download the full package structure."
        Write-Info "Please install git and try again, or clone the repository manually."
        return $false
    }
}


function Main {
    # Check if required arguments were provided
    if (-not $ApiKey -or -not $Domain) {
        Write-Host ""
        Write-ErrorMessage "Missing required arguments"
        Write-Host ""
        Write-Host "Usage:"
        Write-Host "  .\install.ps1 -ApiKey YOUR_API_KEY -Domain YOUR_DOMAIN [-AppName APP_NAME]"
        Write-Host ""
        Write-Host "Or download and run via PowerShell:"
        Write-Host "  Invoke-WebRequest https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/$BRANCH/install.ps1 -OutFile install.ps1"
        Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -ApiKey YOUR_API_KEY -Domain YOUR_DOMAIN"
        Write-Host ""
        exit 1
    }

    # Check Python
    $pythonCmd = Get-PythonCommand
    if (-not $pythonCmd) {
        Write-ErrorMessage "Python 3 is required but not found."
        Write-Info "Please install Python 3 and try again."
        Write-Info "Download from: https://www.python.org/downloads/"
        exit 1
    }

    # Download repository
    if (-not (Get-Repository)) {
        Write-ErrorMessage "Failed to download repository."
        exit 1
    }

    # Change to repository directory
    Push-Location $TEMP_DIR

    try {
        # Build arguments for Python script
        $pythonArgs = @(
            "-m", "scripts.coding_discovery_tools.ai_tools_discovery",
            "--api-key", $ApiKey,
            "--domain", $Domain
        )

        if ($AppName) {
            $pythonArgs += @("--app_name", $AppName)
        }

        # Execute the discovery script
        if ($pythonCmd -eq "py -3") {
            & py -3 @pythonArgs
        } else {
            & $pythonCmd @pythonArgs
        }
    }
    finally {
        Pop-Location
        Remove-TempDirectory
    }
}

Main
