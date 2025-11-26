# Coding Discovery Tool

This repository contains scripts for discovering and extracting coding tool configurations.

## Quick Start

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.sh | bash -s -- --api-key YOUR_API_KEY --domain YOUR_DOMAIN
```

### Windows (PowerShell)

```powershell
$script = Invoke-WebRequest -Uri https://raw.githubusercontent.com/websentry-ai/coding-discovery-tool/main/install.ps1 -UseBasicParsing; Invoke-Expression $script.Content --api-key YOUR_API_KEY --domain YOUR_DOMAIN
```

**Note:** If you encounter execution policy restrictions on Windows, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

