"""
Settings transformers for converting extracted settings to backend API format.

Settings files compose per key across scopes, and each project on a machine gets
its own effective configuration. This module merges every chain the agent would
actually run under, then sends the riskiest one to the backend.

Precedence order (highest to lowest):
    1. managed_plist - macOS MDM plist settings (highest priority)
    2. managed_dropin - Enterprise drop-in override settings
    3. managed - Enterprise/MDM deployed settings
    4. local - User's local overrides (not synced)
    5. project - Project-specific settings
    6. user - Global user settings (lowest priority)
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# Constants for settings source precedence
# Higher number = higher precedence
SETTINGS_PRECEDENCE = {
    "managed_plist": 6,   # Highest priority - macOS MDM plist settings
    "managed_dropin": 5,  # Second priority - enterprise drop-in overrides
    "managed": 4,         # Third priority - enterprise/MDM base settings
    "local": 3,           # Fourth priority - local user overrides
    "project": 2,         # Fifth priority - project settings
    "user": 1,            # Lowest priority - global user settings
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


# Scopes that apply to every project rather than one of them.
GLOBAL_SCOPES = ("user", "managed", "managed_plist", "managed_dropin")

# List-valued permission fields accumulate across a chain; the rest are scalars
# resolved by precedence.
_LIST_FIELDS = ("allow", "deny", "ask", "additionalDirectories")

# Riskiest posture wins when several projects are configured.
_MODE_RANK = {
    "plan": 0,
    "default": 1,
    "acceptEdits": 2,
    "auto": 3,
    "dontAsk": 3,
    "bypassPermissions": 4,
}

# Grants that allow arbitrary execution regardless of the mode.
_UNRESTRICTED_RULES = frozenset(["Bash", "Shell", "Bash(*)", "Shell(*)", "*", "**"])

# Claude Code ignores these at project/local scope and does not fall back to the
# user-scope value either, so the effective mode is not knowable from the files.
# https://code.claude.com/docs/en/permission-modes
_GLOBAL_ONLY_MODES = ("auto", "bypassPermissions")


def _project_key(settings_dict: Dict[str, Any]) -> Optional[str]:
    """Project a non-global settings file belongs to, or None when it is global."""
    if _get_scope_value(settings_dict) in GLOBAL_SCOPES:
        return None
    return str(Path(settings_dict.get("settings_path", "")).parent.parent)


def _is_set(value: Any) -> bool:
    """Whether a value carries content, not just a populated container shape.

    Extractors always emit the `mcp_policies` keys, so a plain truthiness check
    would let an empty project file erase a user-scope policy.
    """
    if isinstance(value, dict):
        return any(_is_set(item) for item in value.values())
    return bool(value)


def _dedupe(values: List[Any]) -> List[Any]:
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _merge_chain(chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge one precedence chain into a single settings dict.

    Scalars take the highest-precedence file that actually sets them; list fields
    accumulate. Mirrors how the agent itself composes settings across scopes.
    """
    chain = sorted(chain, key=lambda s: _get_precedence(_get_scope_value(s)))
    top = chain[-1]

    permissions: Dict[str, Any] = {field: [] for field in _LIST_FIELDS}
    merged: Dict[str, Any] = {
        "scope": _get_scope_value(top),
        "settings_path": top.get("settings_path", ""),
        "raw_settings": top.get("raw_settings", {}),
        "permissions": permissions,
        "sandbox": {},
        "contributing_paths": [],
    }

    disable_auto = False
    for settings_dict in chain:
        source = settings_dict.get("permissions") or {}
        is_global = _get_scope_value(settings_dict) in GLOBAL_SCOPES
        raw_permissions = (settings_dict.get("raw_settings") or {}).get("permissions") or {}
        if raw_permissions.get("disableAutoMode") == "disable":
            disable_auto = True

        mode = source.get("defaultMode")
        if mode and (is_global or mode not in _GLOBAL_ONLY_MODES):
            permissions["defaultMode"] = mode
        elif mode:
            permissions.pop("defaultMode", None)

        for field in _LIST_FIELDS:
            permissions[field].extend(source.get(field) or [])

        sandbox_enabled = (settings_dict.get("sandbox") or {}).get("enabled")
        if sandbox_enabled is not None:
            merged["sandbox"]["enabled"] = sandbox_enabled

        for key in ("mcp_servers", "mcp_policies"):
            if _is_set(settings_dict.get(key)):
                merged[key] = settings_dict[key]

        merged["contributing_paths"].append(settings_dict.get("settings_path", ""))

    if disable_auto and permissions.get("defaultMode") == "auto":
        permissions.pop("defaultMode")

    for field in _LIST_FIELDS:
        permissions[field] = _dedupe(permissions[field])
    return merged


def _effective_settings(settings_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One merged record per project, each seeded with the global scopes."""
    global_settings = [s for s in settings_list if _project_key(s) is None]
    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for settings_dict in settings_list:
        key = _project_key(settings_dict)
        if key is not None:
            by_project.setdefault(key, []).append(settings_dict)

    if not by_project:
        return [_merge_chain(global_settings)] if global_settings else []
    return [_merge_chain(global_settings + chain) for chain in by_project.values()]


def _permissiveness(settings_dict: Dict[str, Any]) -> tuple:
    """Most-permissive-capability-wins, matching how the backend derives autonomy.

    An unrestricted grant runs arbitrary commands whatever the mode says, so it
    has to raise the rank rather than break ties within it.
    """
    permissions = settings_dict.get("permissions") or {}
    allow = permissions.get("allow") or []
    rank = _MODE_RANK.get(permissions.get("defaultMode"), 1)
    if any(str(rule).strip() in _UNRESTRICTED_RULES for rule in allow):
        rank = max(rank, _MODE_RANK["bypassPermissions"])
    return (rank, len(allow))


def _get_highest_precedence_setting(settings_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Riskiest effective configuration across the projects on this machine."""
    effective = _effective_settings(settings_list)
    if not effective:
        return None
    return max(effective, key=_permissiveness)


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

    Files are grouped into one chain per project, each merged per key against the
    global scopes, and the riskiest resulting configuration is returned.

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

    # Map internal scope values to backend-compatible settings_source values
    if scope_value == "local":
        settings_source_value = "user"
    elif scope_value in ("managed_plist", "managed_dropin"):
        settings_source_value = "managed"
    else:
        settings_source_value = scope_value

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

    contributing_paths = highest_precedence.get("contributing_paths")
    if contributing_paths:
        backend_permissions["contributing_paths"] = contributing_paths

    return backend_permissions
