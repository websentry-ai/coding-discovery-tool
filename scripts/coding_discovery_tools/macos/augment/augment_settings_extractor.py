"""
Augment Code settings/permissions extraction for macOS.

Augment persists settings in ``settings.json`` files. This extractor reads:

  - User:    ``~/.augment/settings.json`` (root-aware all-users scan)
  - Managed: ``/etc/augment/settings.json``
  - Project: ``<ws>/.augment/settings.json`` (scope "project") and
             ``<ws>/.augment/settings.local.json`` (scope "local")

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
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    get_top_level_directories,
    is_running_as_root,
    scan_user_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
from .augment import _resolve_augment_dir

logger = logging.getLogger(__name__)

TOOL_NAME = "Augment Code"
AUGMENT_DIR_NAME = ".augment"
SETTINGS_FILENAME = "settings.json"
SETTINGS_LOCAL_FILENAME = "settings.local.json"
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
    """Map ``toolPermissions`` into ``{allow, deny, ask, additionalDirectories}``.

    Each entry is ``{toolName, shellInputRegex?, eventType, permission.type}``; a
    present ``shellInputRegex`` is appended to the tool name as ``toolName(regex)``.
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
        self._extract_project_settings(records)

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

    def _extract_project_settings(self, records: List[Dict]) -> None:
        """Walk for ``<ws>/.augment/settings.json`` + ``settings.local.json``."""
        root_path = self._filesystem_root()
        user_augment_dirs = self._user_augment_dirs()
        try:
            for top_dir in self._iter_top_level_dirs(root_path):
                try:
                    self._walk_for_project_settings(
                        root_path, top_dir, records, user_augment_dirs, current_depth=1
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")

    def _walk_for_project_settings(
        self,
        root_path: Path,
        current_dir: Path,
        records: List[Dict],
        user_augment_dirs,
        current_depth: int = 0,
    ) -> None:
        """Recursively look for ``<project>/.augment/settings*.json`` (bounded).

        The user-home ``~/.augment`` dirs are skipped here (handled as user scope).
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
                        continue
                    if not item.is_dir() or item.is_symlink():
                        continue
                    if item.name == AUGMENT_DIR_NAME:
                        if item.resolve() in user_augment_dirs:
                            continue
                        self._extract_augment_dir_settings(item, records)
                        continue
                    self._walk_for_project_settings(
                        root_path, item, records, user_augment_dirs, current_depth + 1
                    )
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_augment_dir_settings(self, augment_dir: Path, records: List[Dict]) -> None:
        """Read settings.json (project) + settings.local.json (local) in a dir."""
        for filename, scope in (
            (SETTINGS_FILENAME, "project"),
            (SETTINGS_LOCAL_FILENAME, "local"),
        ):
            settings_path = augment_dir / filename
            settings_data = _parse_jsonc(settings_path)
            if settings_data is not None:
                records.append(_build_record(scope, settings_path, settings_data))

    def _user_augment_dirs(self) -> set:
        """Resolved set of user-home ``~/.augment`` dirs to skip in the project walk."""
        dirs = set()

        def collect(user_home: Path) -> None:
            try:
                dirs.add(_resolve_augment_dir(user_home).resolve())
            except (PermissionError, OSError):
                pass

        self._user_settings_scan(collect)
        return dirs

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

    def _filesystem_root(self) -> Path:
        """Root the project walk starts from (POSIX ``/`` on macOS)."""
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under the filesystem root, system dirs excluded."""
        return list(get_top_level_directories(root_path))
