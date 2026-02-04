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

function Write-Info { Write-Host "i " -ForegroundColor Blue -NoNewline; Write-Host $args[0] }
function Write-Success { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $args[0] }
function Write-Warning { Write-Host "[!] " -ForegroundColor Yellow -NoNewline; Write-Host $args[0] }
function Write-ErrorMessage { Write-Host "[X] " -ForegroundColor Red -NoNewline; Write-Host $args[0] }

function Remove-TempDirectory {
    if (Test-Path $TEMP_DIR) { Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue }
}
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Remove-TempDirectory }

function Get-PythonCommand {
    foreach ($cmd in @("python3", "python", "py -3")) {
        try {
            $v = & $cmd.Split(' ')[0] --version 2>&1
            if ($v -match "Python 3") { return $cmd }
        } catch {}
    }
    return $null
}

function Test-GitInstalled {
    try { $null = & git --version 2>&1; return $true } catch { return $false }
}

function Get-Repository {
    if (-not (Test-GitInstalled)) {
        Write-ErrorMessage "Git is not installed."
        return $false
    }
    New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
    
    try {
        & git clone --depth 1 --branch $BRANCH --filter=blob:none --sparse $REPO_URL $TEMP_DIR 2>&1 | Out-Null
        Push-Location $TEMP_DIR
        & git sparse-checkout set scripts/ 2>&1 | Out-Null
        Pop-Location
        return $true
    } catch {
        try {
            Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
            & git clone --depth 1 --branch $BRANCH $REPO_URL $TEMP_DIR 2>&1 | Out-Null
            return $true
        } catch { return $false }
    }
}

# --- MAIN EXECUTION ---
function Main {
    if (-not $ApiKey -or -not $Domain) {
        Write-ErrorMessage "Missing required arguments: -ApiKey and -Domain"
        exit 1
    }

    $CurrentID = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($CurrentID -eq "NT AUTHORITY\SYSTEM") {
        Write-Info "Running as SYSTEM. Attempting to target console user..."
        
        try {
            $SysInfo = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
            $LoggedOnUser = $SysInfo.UserName
            
            if (-not [string]::IsNullOrWhiteSpace($LoggedOnUser)) {
                
                # Handle "DOMAIN\User" vs "User" formats safely
                if ($LoggedOnUser.Contains("\")) {
                    $CleanUser = $LoggedOnUser.Split('\')[-1]
                } else {
                    $CleanUser = $LoggedOnUser
                }

                $UserDir = "C:\Users\$CleanUser"
                
                if (Test-Path $UserDir) {
                    Write-Success "Targeting user profile: $CleanUser"
                    $env:USERPROFILE  = $UserDir
                    $env:APPDATA      = "$UserDir\AppData\Roaming"
                    $env:LOCALAPPDATA = "$UserDir\AppData\Local"
                    $env:HOMEPATH     = "\Users\$CleanUser"
                } else {
                    Write-Warning "User '$CleanUser' detected, but folder '$UserDir' not found."
                }
            } else {
                Write-Warning "No active user logged in. Discovery may return 0 results."
            }
        } catch {
            Write-Warning "Failed to detect user context: $_"
        }
    }

    $pythonCmd = Get-PythonCommand
    if (-not $pythonCmd) { Write-ErrorMessage "Python 3 required but not found."; exit 1 }

    if (-not (Get-Repository)) { Write-ErrorMessage "Failed to download repository."; exit 1 }

    Push-Location $TEMP_DIR
    try {
        $pythonArgs = @("-m", "scripts.coding_discovery_tools.ai_tools_discovery", "--api-key", $ApiKey, "--domain", $Domain)
        if ($AppName) { $pythonArgs += @("--app_name", $AppName) }
        
        $env:PYTHONWARNINGS = "ignore" # Suppress syntax warnings

        if ($pythonCmd -eq "py -3") { & py -3 @pythonArgs } else { & $pythonCmd @pythonArgs }
    }
    finally {
        Pop-Location
        Remove-TempDirectory
    }
}

Main