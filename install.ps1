param(
    [Parameter(Mandatory=$false)]
    [string]$ApiKey,

    [Parameter(Mandatory=$false)]
    [string]$Domain,

    [Parameter(Mandatory=$false)]
    [string]$AppName
)

$REPO_URL = "https://github.com/websentry-ai/coding-discovery-tool.git"

# Ask the backend which branch to run: staging only when it replies "staging",
# otherwise main. Reads the domain from -Domain or UNBOUND_DOMAIN.
$_domain = if ($Domain) { $Domain } else { $env:UNBOUND_DOMAIN }
$_key = if ($ApiKey) { $ApiKey } else { $env:UNBOUND_API_KEY }
# Scheme-less domain -> https (matches the reporting path); never send the key over http.
if ($_domain -and $_domain -notmatch '^https?://') { $_domain = "https://$_domain" }
$BRANCH = 'main'
if ($_domain -and $_key -and $_domain -match '^https://') {
    try {
        $_url = ($_domain.TrimEnd('/')) + '/api/v1/ai-tools/discovery-branch/'
        $_resp = Invoke-RestMethod -Uri $_url -TimeoutSec 5 -Headers @{ Authorization = "Bearer $_key" } -ErrorAction Stop
        if ($_resp.branch -eq 'staging') { $BRANCH = 'staging' }
    } catch { }
}

$TEMP_DIR = Join-Path $env:TEMP "coding-discovery-tool-$(Get-Random)"

function Write-Info { Write-Host "i " -ForegroundColor Blue -NoNewline; Write-Host $args[0] }
function Write-Success { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $args[0] }
function Write-Warning { Write-Host "[!] " -ForegroundColor Yellow -NoNewline; Write-Host $args[0] }
function Write-ErrorMessage { Write-Host "[X] " -ForegroundColor Red -NoNewline; Write-Host $args[0] }

function Remove-TempDirectory {
    if (Test-Path $TEMP_DIR) { Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue }
}
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Remove-TempDirectory }

$script:LastDownloadError = ''

function Send-InstallerFailure {
    # The agent never starts on these paths, so this is the only signal the backend
    # gets; the device would otherwise look like it was never enrolled at all.
    param([string]$Reason, [string]$Detail)
    try {
        if (-not $_key -or -not $_domain) { return }
        $serial = $null
        try { $serial = "$((Get-CimInstance Win32_BIOS -ErrorAction Stop).SerialNumber)".Trim() } catch { }
        if ([string]::IsNullOrWhiteSpace($serial)) { $serial = $env:COMPUTERNAME }
        $body = @{
            device_id  = $serial
            run_id     = [guid]::NewGuid().ToString()
            scan_event = 'failed'
            scan_error = @{
                error_type = 'InstallerPrerequisite'
                reason     = $Reason
                message    = $Detail
                ps_version = $PSVersionTable.PSVersion.ToString()
                branch     = $BRANCH
            }
        } | ConvertTo-Json -Depth 5 -Compress
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
        $null = Invoke-RestMethod -Uri (($_domain.TrimEnd('/')) + '/api/v1/ai-tools/report/') `
            -Method Post -TimeoutSec 10 -Headers @{ Authorization = "Bearer $_key" } `
            -ContentType 'application/json' -Body $body -ErrorAction Stop
    } catch { }
}

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
    try {
        $v = & git --version 2>&1
        return ($LASTEXITCODE -eq 0 -and "$v" -match 'git version')
    } catch { return $false }
}

function Test-RepositoryDownloaded {
    return (Test-Path (Join-Path $TEMP_DIR "scripts"))
}

function Get-RepositoryWithGit {
    New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
    try {
        & git clone --depth 1 --branch $BRANCH --filter=blob:none --sparse $REPO_URL $TEMP_DIR 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Push-Location $TEMP_DIR
            & git sparse-checkout set scripts/ 2>&1 | Out-Null
            Pop-Location
            if (Test-RepositoryDownloaded) { return $true }
        }
        Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
        & git clone --depth 1 --branch $BRANCH $REPO_URL $TEMP_DIR 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0 -and (Test-RepositoryDownloaded))
    } catch { return $false }
}

function Get-RepositoryWithArchive {
    # No Git required: download the branch archive from GitHub and expand it.
    # Mirrors download_with_curl in install.sh. Invoke-WebRequest uses the
    # Windows certificate store, so customer-installed CAs (Zscaler etc.) work.
    $archiveUrl = "https://github.com/websentry-ai/coding-discovery-tool/archive/refs/heads/$BRANCH.zip"
    $zipPath = "$TEMP_DIR.zip"
    $extractDir = "$TEMP_DIR-extract"
    try {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
        Invoke-WebRequest -Uri $archiveUrl -OutFile $zipPath -UseBasicParsing -ErrorAction Stop
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force -ErrorAction Stop
        # The archive has a single top-level folder, e.g. coding-discovery-tool-main
        $root = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
        if (-not $root) { return $false }
        New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
        Get-ChildItem -Path $root.FullName -Force | Move-Item -Destination $TEMP_DIR -Force
        return (Test-RepositoryDownloaded)
    } catch {
        # Say which step failed so certificate, proxy, extraction and disk
        # problems are distinguishable from the log alone.
        $script:LastDownloadError = $_.Exception.Message
        Write-Warning ("Archive download failed: " + $_.Exception.Message)
        return $false
    } finally {
        Remove-Item -Path $zipPath, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-Repository {
    if (Test-GitInstalled) {
        if (Get-RepositoryWithGit) {
            Write-Success "Repository downloaded (via git)"
            return $true
        }
        Write-Warning "Git download failed, trying archive download..."
        Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Info "Git not found, downloading archive instead..."
    }
    if (Get-RepositoryWithArchive) {
        Write-Success "Repository downloaded (via archive)"
        return $true
    }
    if (-not $script:LastDownloadError) { $script:LastDownloadError = 'git and archive download both failed' }
    Write-ErrorMessage "Could not download the repository with git or as an archive."
    return $false
}

# --- MAIN EXECUTION ---
function Main {
    # Accept UNBOUND_API_KEY / UNBOUND_DOMAIN env vars so the scheduled wrapper
    # can invoke this script via -File without putting credentials in the command
    # line (which Win32_Process.CommandLine and Event Log 4688 capture).
    if ([string]::IsNullOrEmpty($ApiKey) -and -not [string]::IsNullOrEmpty($env:UNBOUND_API_KEY)) {
        $ApiKey = $env:UNBOUND_API_KEY
    }
    if ([string]::IsNullOrEmpty($Domain) -and -not [string]::IsNullOrEmpty($env:UNBOUND_DOMAIN)) {
        $Domain = $env:UNBOUND_DOMAIN
    }

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
    if (-not $pythonCmd) {
        Send-InstallerFailure -Reason 'no-python' -Detail 'Python 3 not found in PATH'
        Write-ErrorMessage "Python 3 required but not found."; exit 1
    }

    if (-not (Get-Repository)) {
        Send-InstallerFailure -Reason 'repo-download-failed' -Detail $script:LastDownloadError
        Write-ErrorMessage "Failed to download repository."; exit 1
    }

    Push-Location $TEMP_DIR
    try {
        # NOTE: --api-key appears in the Python process command line (Win32_Process.CommandLine /
        # Event Log 4688). This is a pre-existing limitation of the Python entry point,
        # the wrapper already avoids exposing the key at the PS level.
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