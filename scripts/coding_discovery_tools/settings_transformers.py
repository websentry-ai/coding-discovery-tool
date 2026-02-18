"""
Settings transformers for converting extracted settings to backend API format.

This module transforms individual settings files to the expected backend API structure.
Settings are extracted from multiple sources, and we send the highest precedence one
to the backend.

Precedence order (highest to lowest):
    1. managed - Enterprise/MDM deployed settings (highest priority)
    2. local - User's local overrides (not synced)
    3. project - Project-specific settings
    4. user - Global user settings (lowest priority)
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# Constants for settings source precedence
# Higher number = higher precedence
SETTINGS_PRECEDENCE = {
    "managed": 4,   # Highest priority - enterprise/MDM settings
    "local": 3,     # Second priority - local user overrides
    "project": 2,   # Third priority - project settings
    "user": 1,      # Lowest priority - global user settings
}

# Default precedence for unknown sources
DEFAULT_PRECEDENCE = 0


def _get_scope_value(settings_dict: Dict[str, Any]) -> str:
    """
    Get scope value from settings dict, checking both 'scope' and 'settings_source'.
    """
    return settings_dict.get("scope") or settings_dict.get("settings_source", "user")


def _get_precedence(scope: str) -> int:
    """
    Get precedence value for a scope/settings_source.

    Args:
        scope: Scope type ("managed", "local", "project", or "user")

    Returns:
        Precedence value (higher = more important)
    """
    return SETTINGS_PRECEDENCE.get(scope, DEFAULT_PRECEDENCE)


def _has_permissions(settings_dict: Dict[str, Any]) -> bool:
    """Check if settings dict has actual permission rules defined."""
    permissions = settings_dict.get("permissions", {})
    return bool(
        permissions.get("defaultMode") or
        permissions.get("allow") or
        permissions.get("deny") or
        permissions.get("ask")
    )


def _get_highest_precedence_setting(settings_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Get the settings dict with the highest precedence that has permissions.

    Prioritizes settings with actual permissions defined. Falls back to
    highest precedence overall if no settings have permissions.

    Args:
        settings_list: List of settings dicts

    Returns:
        Settings dict with highest precedence, or None if list is empty
    """
    if not settings_list:
        return None

    settings_with_permissions = [s for s in settings_list if _has_permissions(s)]
    if settings_with_permissions:
        return max(
            settings_with_permissions,
            key=lambda s: _get_precedence(_get_scope_value(s))
        )

    return max(
        settings_list,
        key=lambda s: _get_precedence(_get_scope_value(s))
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
            - scope: "managed", "local", "project", or "user" (preferred)
            - settings_source: Legacy field, same values as scope
            - settings_path: Path to the settings file
            - raw_settings: Full settings JSON (optional, will be read from file if missing)
            - permissions: Dict with permission settings
            - sandbox: Dict with sandbox settings

    Returns:
        Permissions dict in backend format with fields:
            - settings_source: Source type
            - scope: Same as settings_source
            - settings_path: Path to highest precedence file
            - raw_settings: Full settings JSON
            - permission_mode: Mapped from defaultMode
            - allow_rules: Mapped from allow
            - deny_rules: Mapped from deny
            - ask_rules: Mapped from ask
            - sandbox_enabled: Mapped from sandbox.enabled
            - additional_directories: Mapped from additionalDirectories
            - mcp_servers: MCP server configurations (if present)
            - mcp_policies: MCP policies (if present)
        Returns None if no settings provided

    Example:
        >>> settings = [
        ...     {
        ...         "scope": "user",
        ...         "settings_path": "/Users/john/.claude/settings.json",
        ...         "permissions": {"allow": ["Read"], "defaultMode": "default"},
        ...         "sandbox": {"enabled": True}
        ...     },
        ...     {
        ...         "scope": "local",
        ...         "settings_path": "/Users/john/.claude/settings.local.json",
        ...         "permissions": {"allow": ["Bash(npm *)"]}
        ...     }
        ... ]
        >>> result = transform_settings_to_backend_format(settings)
        >>> result["scope"]  # "local" (higher precedence than user)
        >>> result["settings_source"]  # "local" (same, for backend compat)
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
    mcp_servers = highest_precedence.get("mcp_servers")
    mcp_policies = highest_precedence.get("mcp_policies")

    # Get raw settings: prefer from dict, fallback to reading from file
    raw_settings = highest_precedence.get("raw_settings", {})
    if not raw_settings:
        settings_path = Path(highest_precedence.get("settings_path", ""))
        if settings_path:
            raw_settings = _read_raw_settings_from_file(settings_path)

    scope_value = _get_scope_value(highest_precedence)

    settings_source_value = "user" if scope_value == "local" else scope_value

    backend_permissions = {
        "settings_source": settings_source_value,
        "scope": scope_value,
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

    if permissions.get("ask"):
        backend_permissions["ask_rules"] = permissions["ask"]

    if permissions.get("additionalDirectories"):
        backend_permissions["additional_directories"] = permissions["additionalDirectories"]

    if sandbox.get("enabled") is not None:
        backend_permissions["sandbox_enabled"] = sandbox["enabled"]

    # Include MCP servers if present
    if mcp_servers:
        backend_permissions["mcp_servers"] = mcp_servers

    # Include MCP policies if present
    if mcp_policies:
        backend_permissions["mcp_policies"] = mcp_policies

    return backend_permissions
