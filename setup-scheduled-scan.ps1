# =============================================================================
# Unbound Scheduled Run Setup (Windows)
# =============================================================================
#
# Sets up a daily (09:00) Windows Task Scheduler entry that runs either:
#   - unbound discover  (default — backward-compat)
#   - unbound onboard   (when -Command onboard)
#
# Uses only built-in Windows tools: schtasks, cmdkey, PowerShell. No new deps.
#
# Usage:
#   .\setup-scheduled-scan.ps1 -ApiKey <key> -Domain <url>
#   .\setup-scheduled-scan.ps1 -Command onboard -ApiKey <key> -DiscoveryKey <key> -Domain <url>
#   .\setup-scheduled-scan.ps1 -Uninstall
#
# =============================================================================

[CmdletBinding()]
param(
    [ValidateSet('discover','onboard')]
    [string]$Command = 'discover',

    [string]$ApiKey,
    [string]$DiscoveryKey,
    [string]$Domain,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$TaskName       = 'ai.getunbound.scheduled'
$InstallDir     = Join-Path $env:LOCALAPPDATA 'Unbound'
$WrapperScript  = Join-Path $InstallDir 'run-scheduled.ps1'
$LogDir         = Join-Path $InstallDir 'Logs'
$CredTargetBase = 'ai.getunbound.scheduled'

function Show-Usage {
    Write-Host @'
Unbound Scheduled Run Setup (Windows)

Usage:
  Install (discover):  .\setup-scheduled-scan.ps1 -ApiKey <key> -Domain <url>
  Install (onboard):   .\setup-scheduled-scan.ps1 -Command onboard -ApiKey <key> -DiscoveryKey <key> [-Domain <url>]
  Uninstall:           .\setup-scheduled-scan.ps1 -Uninstall

Options:
  -Command <name>     'discover' (default) or 'onboard'
  -ApiKey <key>       User API key (or discovery key when -Command discover)
  -DiscoveryKey <k>   Discovery key (required for -Command onboard)
  -Domain <url>       Backend URL
  -Uninstall          Remove the scheduled task
'@
    exit 1
}

function Store-Credential {
    param([string]$Account, [string]$Secret)
    if ([string]::IsNullOrEmpty($Secret)) { return }
    $target = "$CredTargetBase`:$Account"
    # Remove existing, then add new. cmdkey silently overwrites if same target name,
    # but explicit delete-then-add is more reliable across Windows versions.
    cmdkey /delete:$target *> $null
    $null = cmdkey /generic:$target /user:$Account /pass:$Secret
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to store credential '$Account' in Windows Credential Manager"
    }
}

function Remove-AllCredentials {
    foreach ($acct in @('command','api_key','discovery_key','domain')) {
        cmdkey /delete:"$CredTargetBase`:$acct" *> $null
    }
}

function Create-WrapperScript {
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    if (-not (Test-Path $LogDir))     { New-Item -ItemType Directory -Path $LogDir     -Force | Out-Null }

    # The wrapper reads credentials from Windows Credential Manager at run time.
    # Uses a small inline C# class because PowerShell has no built-in cmdlet for
    # reading generic credentials.
    $wrapper = @'
$ErrorActionPreference = 'Stop'
$LogDir  = Join-Path $env:LOCALAPPDATA 'Unbound\Logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir 'scheduled.log'

function Write-Log($msg) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg | Out-File -FilePath $LogFile -Append -Encoding UTF8
}

Write-Log "=== Starting Unbound scheduled run ==="

# Inline credential reader (no external deps; uses Win32 CredRead via P/Invoke)
$src = @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class CredMgr {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct NATIVE_CREDENTIAL {
        public int Flags; public int Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetName;
        [MarshalAs(UnmanagedType.LPWStr)] public string Comment;
        public long LastWritten; public int CredentialBlobSize;
        public IntPtr CredentialBlob; public int Persist; public int AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetAlias;
        [MarshalAs(UnmanagedType.LPWStr)] public string UserName;
    }
    [DllImport("advapi32.dll", SetLastError = true, EntryPoint = "CredReadW", CharSet = CharSet.Unicode)]
    public static extern bool CredRead(string target, int type, int reservedFlag, out IntPtr CredentialPtr);
    [DllImport("advapi32.dll")] public static extern void CredFree(IntPtr cred);
    public static string Read(string target) {
        IntPtr p;
        if (!CredRead(target, 1, 0, out p)) { return null; }
        try {
            var c = (NATIVE_CREDENTIAL)Marshal.PtrToStructure(p, typeof(NATIVE_CREDENTIAL));
            if (c.CredentialBlobSize == 0) return "";
            var bytes = new byte[c.CredentialBlobSize];
            Marshal.Copy(c.CredentialBlob, bytes, 0, c.CredentialBlobSize);
            return Encoding.Unicode.GetString(bytes);
        } finally { CredFree(p); }
    }
}
"@
Add-Type -TypeDefinition $src -Language CSharp

$Command      = [CredMgr]::Read('ai.getunbound.scheduled:command')
$ApiKey       = [CredMgr]::Read('ai.getunbound.scheduled:api_key')
$DiscoveryKey = [CredMgr]::Read('ai.getunbound.scheduled:discovery_key')
$Domain       = [CredMgr]::Read('ai.getunbound.scheduled:domain')

if ([string]::IsNullOrEmpty($Command) -or [string]::IsNullOrEmpty($ApiKey)) {
    Write-Log "ERROR: Required credentials missing from Credential Manager"
    exit 1
}

switch ($Command) {
    'discover' {
        if ([string]::IsNullOrEmpty($Domain)) { Write-Log "ERROR: Domain missing for discover"; exit 1 }
        $installPs1 = 'https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.ps1'
        $tmp = Join-Path $env:TEMP ("unbound-install-{0}.ps1" -f ([Guid]::NewGuid().ToString('N')))
        Write-Log "Downloading install.ps1 to $tmp"
        try {
            Invoke-WebRequest -Uri $installPs1 -OutFile $tmp -UseBasicParsing
            Write-Log "Executing install.ps1"
            & powershell -NoProfile -ExecutionPolicy Bypass -File $tmp -ApiKey $ApiKey -Domain $Domain *>> $LogFile
            $ec = $LASTEXITCODE
        } finally {
            if (Test-Path $tmp) { Remove-Item $tmp -ErrorAction SilentlyContinue }
        }
        Write-Log ("Discover exited with code {0}" -f $ec)
    }
    'onboard' {
        $unbound = (Get-Command unbound -ErrorAction SilentlyContinue).Source
        if (-not $unbound) {
            Write-Log "ERROR: 'unbound' CLI not found in PATH. Install with: npm install -g unbound-cli"
            exit 1
        }
        $args = @('onboard', '--api-key', $ApiKey, '--discovery-key', $DiscoveryKey)
        if (-not [string]::IsNullOrEmpty($Domain)) { $args += @('--domain', $Domain) }
        Write-Log "Executing: unbound onboard --api-key *** --discovery-key *** ..."
        & $unbound @args *>> $LogFile
        $ec = $LASTEXITCODE
        Write-Log ("Onboard exited with code {0}" -f $ec)
    }
    default {
        Write-Log ("ERROR: Unknown command '{0}'" -f $Command)
        exit 1
    }
}

Write-Log "=== Finished ==="
'@

    Set-Content -Path $WrapperScript -Value $wrapper -Encoding UTF8
    Write-Host "  Wrapper script created: $WrapperScript"
}

function Install-ScheduledTask {
    Store-Credential -Account 'command'       -Secret $Command
    Store-Credential -Account 'api_key'       -Secret $ApiKey
    if ($DiscoveryKey) { Store-Credential -Account 'discovery_key' -Secret $DiscoveryKey }
    if ($Domain)       { Store-Credential -Account 'domain'        -Secret $Domain }
    Write-Host "  Credentials stored in Windows Credential Manager"

    Create-WrapperScript

    # Remove existing task if present
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Write-Host "Removing previous scheduled task..."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    # Build task: Register-ScheduledTask supports StartWhenAvailable, which makes
    # Windows run the task as soon as possible after a missed scheduled start.
    # This is the Windows equivalent of systemd's Persistent=true / launchd's
    # automatic catch-up behaviour.
    $action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $WrapperScript)
    $trigger   = New-ScheduledTaskTrigger -Daily -At '09:00'
    $settings  = New-ScheduledTaskSettingsSet `
                    -StartWhenAvailable `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName  $TaskName `
        -Action    $action `
        -Trigger   $trigger `
        -Settings  $settings `
        -Principal $principal `
        -Force | Out-Null

    Write-Host ""
    Write-Host "Unbound scheduled run set up."
    Write-Host "  Command:     $Command"
    Write-Host "  Schedule:    Daily at 09:00 (catches up missed runs)"
    Write-Host "  Logs:        $LogDir\scheduled.log"
    Write-Host "  Uninstall:   .\setup-scheduled-scan.ps1 -Uninstall"
}

function Uninstall-ScheduledTask {
    Write-Host "Uninstalling Unbound scheduled run..."
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "  Removed scheduled task"
    }
    Remove-AllCredentials
    Write-Host "  Removed credentials"
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "  Removed install directory"
    }
    Write-Host "Done."
}

# =============================================================================
# Main
# =============================================================================

if ($Uninstall) {
    Uninstall-ScheduledTask
    exit 0
}

if ([string]::IsNullOrEmpty($ApiKey)) {
    Write-Host "Error: -ApiKey is required"
    Show-Usage
}
if ($Command -eq 'onboard' -and [string]::IsNullOrEmpty($DiscoveryKey)) {
    Write-Host "Error: -DiscoveryKey is required when -Command onboard"
    Show-Usage
}
if ($Command -eq 'discover' -and [string]::IsNullOrEmpty($Domain)) {
    Write-Host "Error: -Domain is required when -Command discover"
    Show-Usage
}

Install-ScheduledTask
