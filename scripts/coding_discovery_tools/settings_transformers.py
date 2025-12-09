"""
Settings transformers for converting extracted settings to backend API format.

This module transforms individual settings files to the expected backend API structure.
Settings are extracted from multiple sources (user, project), and we send
the highest precedence one to the backend.

Precedence order (highest to lowest):
    1. user - Global user settings (highest priority)
    2. project - Project-specific settings
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# Constants for settings source precedence
# Higher number = higher precedence
SETTINGS_PRECEDENCE = {
    "user": 2,      # Highest priority - user settings
    "project": 1,  # Second priority - project settings
}

# Default precedence for unknown sources
DEFAULT_PRECEDENCE = 0


def _get_precedence(settings_source: str) -> int:
    """
    Get precedence value for a settings source.
    
    Args:
        settings_source: Source type ("user" or "project")
        
    Returns:
        Precedence value (higher = more important)
    """
    return SETTINGS_PRECEDENCE.get(settings_source, DEFAULT_PRECEDENCE)


def _get_highest_precedence_setting(settings_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Get the settings dict with the highest precedence.
    
    Args:
        settings_list: List of settings dicts
        
    Returns:
        Settings dict with highest precedence, or None if list is empty
    """
    if not settings_list:
        return None
    
    return max(
        settings_list,
        key=lambda s: _get_precedence(s.get("settings_source", "user"))
    )


def _read_raw_settings_from_file(settings_path: Path) -> Dict[str, Any]:
    """
    Read and parse raw settings JSON from a file.
    
    Args:
        settings_path: Path to the settings JSON file
        
    Returns:
        Parsed JSON as dict, or empty dict if file cannot be read
    """
    if not settings_path.exists():
        logger.debug(f"Settings file does not exist: {settings_path}")
        return {}
    
    try:
        content = settings_path.read_text(encoding='utf-8', errors='replace')
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in settings file {settings_path}: {e}")
        return {}
    except Exception as e:
        logger.debug(f"Could not read raw settings from {settings_path}: {e}")
        return {}


def transform_settings_to_backend_format(settings_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Transform extracted settings list to backend API format.
    
    This function selects the highest precedence settings file and transforms it
    to the backend format. No merging is performed - we simply extract and send
    the settings as-is from the highest precedence source.
    
    Args:
        settings_list: List of settings dicts from extractor. Each dict should contain:
            - settings_source: "user" or "project"
            - settings_path: Path to the settings file
            - raw_settings: Full settings JSON (optional, will be read from file if missing)
            - permissions: Dict with permission settings
            - sandbox: Dict with sandbox settings
            
    Returns:
        Permissions dict in backend format with fields:
            - settings_source: Source type (from highest precedence)
            - settings_path: Path to highest precedence file
            - raw_settings: Full settings JSON
            - permission_mode: Mapped from defaultMode
            - allow_rules: Mapped from allow
            - deny_rules: Mapped from deny
            - sandbox_enabled: Mapped from sandbox.enabled
            - additional_directories: Mapped from additionalDirectories
        Returns None if no settings provided
        
    Example:
        >>> settings = [
        ...     {
        ...         "settings_source": "user",
        ...         "settings_path": "/Users/john/.claude/settings.json",
        ...         "permissions": {"allow": ["Read"], "defaultMode": "default"},
        ...         "sandbox": {"enabled": True}
        ...     },
        ...     {
        ...         "settings_source": "project",
        ...         "settings_path": "/project/.claude/settings.json",
        ...         "permissions": {"allow": ["Bash(npm *)"]}
        ...     }
        ... ]
        >>> result = transform_settings_to_backend_format(settings)
        >>> result["settings_source"]  # "project" (higher precedence)
        >>> result["permission_mode"]  # None (not in project settings)
        >>> result["allow_rules"]  # ["Bash(npm *)"]
    """
    if not settings_list:
        return None
    
    # Get highest precedence setting (no merging, just pick the best one)
    highest_precedence = _get_highest_precedence_setting(settings_list)
    if not highest_precedence:
        return None
    
    # Extract values from the highest precedence setting
    permissions = highest_precedence.get("permissions", {})
    sandbox = highest_precedence.get("sandbox", {})
    
    # Get raw settings: prefer from dict, fallback to reading from file
    raw_settings = highest_precedence.get("raw_settings", {})
    if not raw_settings:
        settings_path = Path(highest_precedence.get("settings_path", ""))
        if settings_path:
            raw_settings = _read_raw_settings_from_file(settings_path)
    
    # Build backend format - just map the fields from the selected settings file
    backend_permissions = {
        "settings_source": highest_precedence.get("settings_source", "user"),
        "settings_path": highest_precedence.get("settings_path", ""),
        "raw_settings": raw_settings,
    }
    
    # Map permission fields to backend format (only if present)
    if permissions.get("defaultMode"):
        backend_permissions["permission_mode"] = permissions["defaultMode"]
    
    if permissions.get("allow"):
        backend_permissions["allow_rules"] = permissions["allow"]
    
    if permissions.get("deny"):
        backend_permissions["deny_rules"] = permissions["deny"]
    
    if permissions.get("additionalDirectories"):
        backend_permissions["additional_directories"] = permissions["additionalDirectories"]
    
    # Map sandbox_enabled (only the enabled field, not full sandbox_settings)
    if sandbox.get("enabled") is not None:
        backend_permissions["sandbox_enabled"] = sandbox["enabled"]
    
    return backend_permissions
