"""
GitHub Copilot CLI settings/permissions extraction for macOS.

For the standalone ``@github/copilot`` CLI (config under ``~/.copilot/``). The
CLI persists only a small set of durable permission signals on disk — verified
against the v1.0.55 binary:

  - ``trusted_folders`` : directories the user has trusted (workspace trust)
  - ``allowed_urls``    : network allow-list
  - ``denied_urls``     : network deny-list

These live in ``<config_dir>/config.json`` and are being migrated into
``<config_dir>/settings.json`` (so BOTH are read; settings.json wins on
conflict). Keys are snake_case on disk; the docs prose uses camelCase, so both
spellings are tolerated. Shell/write/MCP tool "always-allow" approvals are
session-only and never written to disk, so they are intentionally NOT collected.

``permissions-config.json`` (documented per-project tool+dir permissions) is not
present in v1.0.55; it is probed and, if found, stashed under
``raw_settings["permissions_config"]`` for forward compatibility.

The record uses the Claude-style nested ``permissions`` shape and is routed by
the orchestrator through ``transform_settings_to_backend_format`` into the
tool-level ``permissions`` payload — so NO backend or frontend change is needed.
``<config_dir>`` is resolved via the shared ``_resolve_copilot_dir`` (honors
``COPILOT_HOME`` for the running user).
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseCopilotCliSettingsExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories
from .copilot_cli import _resolve_copilot_dir
from .mcp_config_extractor import _strip_jsonc_comments, _strip_trailing_commas

logger = logging.getLogger(__name__)

TOOL_NAME = "GitHub Copilot CLI"
CONFIG_FILENAME = "config.json"
SETTINGS_FILENAME = "settings.json"
# Documented per-project tool+dir permission store — NOT present in v1.0.55.
PERMISSIONS_CONFIG_FILENAME = "permissions-config.json"

# Durable permission keys. Snake_case is what the binary writes; camelCase is the
# docs-prose spelling — tolerate both.
_TRUSTED_FOLDERS_KEYS = ("trusted_folders", "trustedFolders")
_ALLOWED_URLS_KEYS = ("allowed_urls", "allowedUrls")
_DENIED_URLS_KEYS = ("denied_urls", "deniedUrls")


def _parse_jsonc(path: Path) -> Optional[Dict]:
    """Leniently parse a JSON/JSONC file (comments + trailing commas tolerated).

    Returns the parsed object, or None if the file is missing/unreadable/not a
    dict. Never raises — this runs on customer machines.
    """
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _strip_trailing_commas(_strip_jsonc_comments(raw))
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except (PermissionError, OSError) as e:
        logger.debug(f"Permission/OS error reading {path}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Could not parse {path}: {e}")
        return None


def _resolve_list(override: Optional[Dict], primary: Optional[Dict], keys) -> List[str]:
    """Resolve a string-list value, preferring ``override`` (settings.json) when it
    carries the key, else ``primary`` (config.json). Presence — not truthiness —
    decides, so an explicit empty list in settings.json wins over config.json.
    """
    for source in (override, primary):
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                value = source[key]
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, str)]
                return []
    return []


class MacOSCopilotCliSettingsExtractor(BaseCopilotCliSettingsExtractor):
    """Extractor for GitHub Copilot CLI durable permissions on macOS."""

    def extract_settings(self) -> Optional[List[Dict]]:
        records: List[Dict] = []

        def extract_for_user(user_home: Path) -> None:
            try:
                record = self._extract_for_user(user_home)
                if record:
                    records.append(record)
            except Exception as e:
                logger.debug(f"Error extracting Copilot CLI settings for {user_home}: {e}")

        self._scan_all_user_homes(extract_for_user)
        return records

    def _extract_for_user(self, user_home: Path) -> Optional[Dict]:
        """Build one user-scope settings record from the user's config dir.

        Returns None unless ``config.json`` or ``settings.json`` exists, so an
        install with neither produces no row — but an install WITH a config file
        and no explicit permissions still surfaces its default/interactive posture.
        """
        config_dir = _resolve_copilot_dir(user_home)
        config_path = config_dir / CONFIG_FILENAME
        settings_path = config_dir / SETTINGS_FILENAME

        config_data = _parse_jsonc(config_path)
        settings_data = _parse_jsonc(settings_path)
        if config_data is None and settings_data is None:
            return None

        trusted_folders = _resolve_list(settings_data, config_data, _TRUSTED_FOLDERS_KEYS)
        allowed_urls = _resolve_list(settings_data, config_data, _ALLOWED_URLS_KEYS)
        denied_urls = _resolve_list(settings_data, config_data, _DENIED_URLS_KEYS)

        # raw_settings feeds the backend risk classifier. Start from the FULL
        # settings.json — Copilot's "personal settings" file, which also holds
        # autonomy/security flags (e.g. continueOnAutoMode, askUser,
        # storeTokenPlaintext) — so the classifier sees those signals and
        # auto-picks up any new setting GitHub adds, without a code change.
        # SECURITY: we deliberately do NOT dump config.json — it holds auth state
        # and other internal data; only its permission keys are lifted (the
        # resolved trusted_folders, layered below).
        # The resolved permission keys are layered on top in canonical snake_case
        # (reflecting the settings.json-wins merge) and guarantee raw_settings is
        # always a non-empty dict, so the transformer never re-reads the file with
        # strict json.loads (which would choke on JSONC).
        raw_settings: Dict = dict(settings_data) if isinstance(settings_data, dict) else {}
        raw_settings["trusted_folders"] = trusted_folders
        raw_settings["allowed_urls"] = allowed_urls
        raw_settings["denied_urls"] = denied_urls

        # Forward-compat: surface permissions-config.json if a future CLI writes it.
        permissions_config = _parse_jsonc(config_dir / PERMISSIONS_CONFIG_FILENAME)
        if permissions_config is not None:
            raw_settings["permissions_config"] = permissions_config

        # settings_path should reference the file whose content backs this record:
        # settings.json (the user-editable "personal settings" file that feeds
        # raw_settings) when present, else config.json. config.json is deliberately
        # NOT read for content (it holds auth state — only its permission keys are
        # lifted), so labeling the path with it would misrepresent the source.
        settings_file = settings_path if settings_data is not None else config_path
        return {
            "tool_name": TOOL_NAME,
            "scope": "user",
            "settings_path": str(settings_file),
            "raw_settings": raw_settings,
            "permissions": {
                "additionalDirectories": trusted_folders,
                "allow": allowed_urls,
                "deny": denied_urls,
            },
        }

    # -- OS-specific seam (overridden by the Windows subclass) ----------------

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every user home (all users when root)."""
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())
