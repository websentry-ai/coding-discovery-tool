"""
Augment Code settings/permissions extraction for macOS.

Augment persists settings in ``settings.json`` files. This extractor reads:

  - User:    ``~/.augment/settings.json`` (root-aware all-users scan)
  - Managed: ``/etc/augment/settings.json``

Project/local-scope settings (``<ws>/.augment/settings.json`` /
``settings.local.json``) are intentionally NOT collected: they cannot be
surfaced in the tool-level ``permissions`` blob the backend supports (the
consumer keeps only the user-row + managed scopes), so an expensive
whole-filesystem walk to extract records that are then dropped is avoided.

``toolPermissions`` is parsed into ``permissions.{allow,deny,ask}`` (an array of
``{toolName, shellInputRegex?, eventType, permission.type}``; a present
``shellInputRegex`` is appended to the tool name as ``toolName(regex)``). The FULL
parsed settings JSON — including ``hooks`` — is preserved in ``raw_settings`` so
the backend risk classifier sees those signals (``transform_settings_to_backend_format``
does NOT lift hooks, so they MUST ride inside ``raw_settings``).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...coding_tool_base import BaseAugmentSettingsExtractor
from ...macos_extraction_helpers import (
    is_running_as_root,
    scan_user_directories,
)
from ...mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
from .augment import _resolve_augment_dir

logger = logging.getLogger(__name__)

TOOL_NAME = "Augment Code"
SETTINGS_FILENAME = "settings.json"
TOOL_PERMISSIONS_KEY = "toolPermissions"
# Cap an over-large settings file so a runaway file can't blow up the payload.
_MAX_SETTINGS_BYTES = 1_000_000

# permission.type -> permissions bucket.
_PERMISSION_TYPE_TO_BUCKET = {
    "allow": "allow",
    "deny": "deny",
    "ask-user": "ask",
}


def _parse_jsonc(path: Path) -> Optional[Dict]:
    """Leniently parse a JSON/JSONC settings file. None on any failure.

    Over-large files are truncated-and-skipped (warned), invalid JSON is warned
    and skipped. Never raises — this runs on customer machines.
    """
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > _MAX_SETTINGS_BYTES:
            logger.warning(f"Skipping oversize Augment settings file {path} ({size} bytes)")
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_trailing_commas(_strip_jsonc_comments(raw))
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except (PermissionError, OSError) as e:
        logger.debug(f"Permission/OS error reading {path}: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Augment settings {path}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Could not parse {path}: {e}")
        return None


def _parse_tool_permissions(settings_data: Dict[str, Any]) -> Dict[str, List[str]]:
    """Map ``toolPermissions`` into ``{allow, deny, ask}``.

    Each entry is ``{toolName, shellInputRegex?, eventType, permission.type}``; a
    present ``shellInputRegex`` is appended to the tool name as ``toolName(regex)``.

    ``additionalDirectories`` is always emitted EMPTY (kept for permissions-dict
    shape parity with the other extractors): Augment has no trusted-folders /
    additional-directories concept, so nothing populates it.
    """
    buckets: Dict[str, List[str]] = {"allow": [], "deny": [], "ask": [], "additionalDirectories": []}
    entries = settings_data.get(TOOL_PERMISSIONS_KEY)
    if not isinstance(entries, list):
        return buckets

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        permission = entry.get("permission")
        ptype = permission.get("type") if isinstance(permission, dict) else None
        bucket = _PERMISSION_TYPE_TO_BUCKET.get(ptype)
        if bucket is None:
            continue
        tool = entry.get("toolName")
        if not isinstance(tool, str) or not tool:
            continue
        regex = entry.get("shellInputRegex")
        label = f"{tool}({regex})" if isinstance(regex, str) and regex else tool
        buckets[bucket].append(label)

    return buckets


def _build_record(scope: str, settings_path: Path, settings_data: Dict[str, Any]) -> Dict:
    """Build one per-scope settings record (hooks preserved in raw_settings)."""
    buckets = _parse_tool_permissions(settings_data)
    return {
        "tool_name": TOOL_NAME,
        "scope": scope,
        "settings_path": str(settings_path),
        "raw_settings": settings_data,
        "permissions": {
            "defaultMode": None,
            "allow": buckets["allow"],
            "deny": buckets["deny"],
            "ask": buckets["ask"],
            "additionalDirectories": buckets["additionalDirectories"],
        },
        "mcp_servers": list(settings_data["mcpServers"].keys())
        if isinstance(settings_data.get("mcpServers"), dict) else [],
        "mcp_policies": {"allowedMcpServers": [], "deniedMcpServers": []},
        "sandbox": {"enabled": None},
    }


class MacOSAugmentSettingsExtractor(BaseAugmentSettingsExtractor):
    """Extractor for Augment Code settings on macOS systems."""

    def extract_settings(self) -> Optional[List[Dict]]:
        records: List[Dict] = []

        self._extract_user_settings(records)
        self._extract_managed_settings(records)

        return records

    def _extract_user_settings(self, records: List[Dict]) -> None:
        """Extract ``~/.augment/settings.json`` (all users when root)."""
        def extract_for_user(user_home: Path) -> None:
            try:
                settings_path = _resolve_augment_dir(user_home) / SETTINGS_FILENAME
                settings_data = _parse_jsonc(settings_path)
                if settings_data is not None:
                    records.append(_build_record("user", settings_path, settings_data))
            except Exception as e:
                logger.debug(f"Error extracting user Augment settings for {user_home}: {e}")

        self._user_settings_scan(extract_for_user)

    def _extract_managed_settings(self, records: List[Dict]) -> None:
        """Extract the managed (org-level) ``/etc/augment/settings.json``."""
        try:
            managed_path = self._managed_settings_path()
            settings_data = _parse_jsonc(managed_path)
            if settings_data is not None:
                records.append(_build_record("managed", managed_path, settings_data))
        except Exception as e:
            logger.debug(f"Error extracting managed Augment settings: {e}")

    # -- OS-specific seams (overridden by the Windows/Linux subclasses) -------

    def _user_settings_scan(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every user home (all users when root)."""
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _managed_settings_path(self) -> Path:
        """Managed (org-level) settings file path."""
        return Path("/etc/augment/settings.json")
