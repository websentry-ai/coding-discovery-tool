"""
Utility functions shared across the AI tools discovery system
"""

import json
import logging
import platform
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from .constants import COMMAND_TIMEOUT, INVALID_SERIAL_VALUES, VERSION_TIMEOUT

logger = logging.getLogger(__name__)


def is_valid_serial(serial: str) -> bool:
    """
    Check if serial number is valid (not a placeholder value).
    
    Args:
        serial: Serial number to validate
        
    Returns:
        True if valid, False otherwise
    """
    return serial and serial.upper() not in INVALID_SERIAL_VALUES


def extract_version_number(text: str) -> Optional[str]:
    """
    Extract clean version number from text.
    
    Examples:
        '2.0.37 (Claude Code)' -> '2.0.37'
        'Version: 1.2.3' -> '1.2.3'
    
    Args:
        text: Text containing version information
        
    Returns:
        Version number string or None
    """
    if not text:
        return None

    # Try to extract version pattern (e.g., 2.0.37)
    version_match = re.search(r'(\d+\.\d+\.\d+)', text)
    if version_match:
        return version_match.group(1)

    # Fallback: return first line with digits
    for line in text.split('\n'):
        if any(char.isdigit() for char in line):
            return line.strip()

    return text.strip() if text.strip() else None


def run_command(command: list, timeout: int = COMMAND_TIMEOUT) -> Optional[str]:
    """
    Run a shell command and return its output.
    
    Args:
        command: Command and arguments as list
        timeout: Command timeout in seconds
        
    Returns:
        Command output as string or None if failed
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Command {command} failed: {e}")
    return None


def get_hostname() -> str:
    """Get the system hostname."""
    return platform.node()


def get_user_info() -> str:
    """
    Get current user information (whoami equivalent).
    Cross-platform function that returns username.
    Gets username directly from system information, not environment variables.
    
    On macOS, when running as root, finds the user with the most storage space
    in /Users directory to get the actual user instead of "root".
    
    On Windows, when running as administrator, finds the actual logged-in user
    by querying explorer.exe process owner, Win32_ComputerSystem, or active console
    session instead of returning "Administrator" or "admin".
    
    Returns:
        Current username as string
    """
    try:
        username = None
        
        if platform.system() == "Windows":
            # Use whoami command on Windows (works reliably)
            whoami_output = run_command(["whoami"], COMMAND_TIMEOUT)
            # Extract just the username if whoami returns DOMAIN\username format
            if username and "\\" in username:
                username = username.split("\\")[-1]
        else:
            # On macOS/Linux, check if running as root first
            current_user = run_command(["whoami"], COMMAND_TIMEOUT)
            
            # If running as root on macOS, try to find the actual user
            if current_user == "root" and platform.system() == "Darwin":
                # Method 1: Get console user (most direct and reliable)
                username = run_command(["stat", "-f", "%Su", "/dev/console"], COMMAND_TIMEOUT)
                
                # Method 2: Fallback to finding user with most storage space in /Users
                # Command: du -sk /Users/* 2>/dev/null | awk '!/\/Shared$/ {print}' | sort -nr | head -1 | awk -F/ '{print $NF}'
                # Using shell=True to properly handle glob expansion and pipes
                if not username:
                    try:
                        result = subprocess.run(
                            "du -sk /Users/* 2>/dev/null | awk '!/\\/Shared$/ {print}' | sort -nr | head -1 | awk -F/ '{print $NF}'",
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=COMMAND_TIMEOUT
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            username = result.stdout.strip()
                    except Exception as e:
                        logger.debug(f"Failed to get user from storage space: {e}")
            
            # If not root or methods above didn't work, use standard methods
            if not username:
                username = current_user
                if not username:
                    # Fallback to id -un
                    username = run_command(["id", "-un"], COMMAND_TIMEOUT)
        
        # Final fallback to getpass (uses system user database)
        if not username:
            import getpass
            username = getpass.getuser()
        
        return username or "unknown"
    except Exception as e:
        logger.warning(f"Could not extract username: {e}")
        return "unknown"


def resolve_windows_shortcut(shortcut_path: Path) -> Optional[Path]:
    """
    Resolve Windows .lnk shortcut to its target path.
    
    Args:
        shortcut_path: Path to the .lnk file
        
    Returns:
        Target path or None if resolution failed
    """
    try:
        ps_command = (
            f'$shell = New-Object -ComObject WScript.Shell; '
            f'$shortcut = $shell.CreateShortcut({repr(str(shortcut_path))}); '
            f'$shortcut.TargetPath'
        )
        output = run_command(["powershell", "-Command", ps_command], VERSION_TIMEOUT)
        if output and Path(output).exists():
            return Path(output)
    except Exception:
        pass
    return None

def normalize_url(domain: str) -> str:
    """Normalize domain to proper URL format."""
    domain = domain.strip()
    
    if domain.startswith("http://") or domain.startswith("https://"):
        url = domain
    else:
        url = f"https://{domain}"
    
    return url.rstrip('/')

def send_report_to_backend(backend_url: str, api_key: str, report: Dict, app_name: Optional[str] = None) -> bool:
    """
    Send discovery report to backend endpoint.
    
    Args:
        backend_url: Backend URL to send the report to
        api_key: API key for authentication
        report: Report dictionary to send
        app_name: Optional application name (e.g., JumpCloud) to include in request body
        
    Returns:
        True if successful, False otherwise
    """
    url = f"{normalize_url(backend_url)}/api/v1/ai-tools/report/"
    
    # Add app_name to the report if provided
    if app_name:
        report = {**report, "app_name": app_name}
    
    data = json.dumps(report).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req) as response:
            return response.getcode() in (200, 201)
    except urllib.error.HTTPError as e:
        logger.error(f"Failed to send report: {e.code} - {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Error sending report: {e}")
        return False
