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

# Same directory and line format as setup-scheduled-scan.ps1's scheduled.log, so installer and
# scheduled-run failures sit side by side. Under MDM the console output goes nowhere.
$LogDir = Join-Path $env:LOCALAPPDATA 'Unbound\Logs'
$script:InstallLog = Join-Path $LogDir 'install.log'

function Format-Detail($text) {
    # Exception text is third-party and unbounded. Flatten so one entry stays one line,
    # redact the key in case a URI ever carries it, and cap at 1024 like the Python side
    # does for response_body/curl_stderr.
    if (-not $text) { return '' }
    $s = [string]$text -replace '[\r\n]+', ' '
    if ($_key) { $s = $s -replace [regex]::Escape($_key), '<redacted>' }
    if ($s.Length -gt 1024) { $s = $s.Substring(0, 1024) }
    return $s
}

function Write-Log($msg) {
    try {
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
        "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), (Format-Detail $msg) |
            Out-File -FilePath $script:InstallLog -Append -Encoding UTF8
    } catch { }
}

function Write-Info { Write-Host "i " -ForegroundColor Blue -NoNewline; Write-Host $args[0]; Write-Log $args[0] }
function Write-Success { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $args[0]; Write-Log $args[0] }
function Write-Warning { Write-Host "[!] " -ForegroundColor Yellow -NoNewline; Write-Host $args[0]; Write-Log "WARNING: $($args[0])" }
function Write-ErrorMessage {
    Write-Host "[X] " -ForegroundColor Red -NoNewline; Write-Host $args[0]
    Write-Log "ERROR: $($args[0])"
    # Say where the detail went; without this the customer has to know the convention.
    Write-Host "    Details written to $script:InstallLog" -ForegroundColor DarkGray
}

function Remove-TempDirectory {
    if (Test-Path $TEMP_DIR) { Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue }
}
$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action { Remove-TempDirectory }

$script:LastDownloadError = ''

$SENTRY_DSN = if ($env:AI_DISCOVERY_SENTRY_DSN) { $env:AI_DISCOVERY_SENTRY_DSN }
              else { 'https://62a73a0043568547cb63a35394b63906@o4509196569149440.ingest.us.sentry.io/4510874666663936' }

function Send-InstallerFailure {
    # install.ps1 exits before the agent exists, so utils.py's reporter cannot be used.
    param([string]$Reason, [string]$Detail)
    try {
        if (-not $SENTRY_DSN -or -not $_domain) { return }
        $key, $hostProject = ($SENTRY_DSN -replace '^https://', '') -split '@', 2
        $sentryHost, $projectId = $hostProject -split '/', 2
        # Raw BIOS value, not the agent's resolved device_id; hostname is the join key.
        $serial = ''
        try { $serial = "$((Get-WmiObject Win32_BIOS -ErrorAction Stop).SerialNumber)".Trim() } catch { }
        $body = @{
            event_id  = [guid]::NewGuid().ToString('N')
            timestamp = (Get-Date).ToUniversalTime().ToString('o')
            level     = 'warning'
            platform  = 'other'
            sdk       = @{ name = 'install.ps1'; version = '1.0.0' }
            tags      = @{
                phase       = 'installer_blocked'
                reason      = $Reason
                os          = 'Windows'
                hostname    = $env:COMPUTERNAME
                bios_serial = $serial
                domain      = $_domain
                branch      = $BRANCH
                ps_version  = $PSVersionTable.PSVersion.ToString()
            }
            # Reason only in the title; detail varies per machine and would split the issue.
            exception = @{ values = @(@{ type = 'InstallerPrerequisite'; value = "Discovery installer blocked: $Reason" }) }
            extra     = @{ detail = (Format-Detail $Detail) }
        } | ConvertTo-Json -Depth 6 -Compress
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
        $null = Invoke-RestMethod -Uri "https://$sentryHost/api/$projectId/store/" -Method Post -TimeoutSec 10 `
            -Headers @{ 'X-Sentry-Auth' = "Sentry sentry_version=7, sentry_key=$key, sentry_client=install.ps1/1.0.0" } `
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
        # Reports only when the domain resolved; without it there is nothing to attribute
        # the event to, so a missing -Domain stays unreportable by construction.
        Send-InstallerFailure -Reason 'missing-arguments' `
            -Detail "ApiKey supplied: $([bool]$ApiKey); Domain supplied: $([bool]$Domain)"
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