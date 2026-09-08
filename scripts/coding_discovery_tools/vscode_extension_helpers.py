"""Shared helpers for reading the VS Code extension install registry.

VS Code-family editors rewrite ``<extensions-dir>/extensions.json`` on uninstall,
making it the authoritative "is this extension live?" signal — unlike the
extension's ``globalStorage/<ext-id>`` dir, which survives uninstall
(microsoft/vscode#119022) and so produces phantom rows. The extensions dir is
home-relative and identical across macOS/Windows/Linux, so one mapping serves all.

All I/O is wrapped — this runs on customer machines and must never raise.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Per-user extensions dir per VS Code-family editor, keyed by SUPPORTED_IDES folder
# name. Home-relative and identical on all OSes (VS Code uses ~/.vscode/extensions
# cross-platform, not the ~/.config app-data tree).
_EXTENSIONS_DIR_BY_EDITOR = {
    "Code": ".vscode/extensions",
    "Code - Insiders": ".vscode-insiders/extensions",
    "Cursor": ".cursor/extensions",
    "Windsurf": ".windsurf/extensions",
    "VSCodium": ".vscode-oss/extensions",
    "Antigravity": ".antigravity/extensions",
}

# For callers that scan every editor rather than a fixed SUPPORTED_IDES subset.
VSCODE_EDITOR_KEYS = tuple(_EXTENSIONS_DIR_BY_EDITOR)

# Row label per user-data dir name. Insiders is folded into ``Code`` downstream.
VSCODE_EDITOR_DISPLAY_NAMES = {
    "Code": "VS Code",
    "Cursor": "Cursor",
    "Windsurf": "Windsurf",
    "VSCodium": "VSCodium",
    "Antigravity": "Antigravity",
}

_EDITOR_KEY_BY_DISPLAY = {
    display.lower(): key for key, display in VSCODE_EDITOR_DISPLAY_NAMES.items()
}
_ROW_EDITOR_SUFFIX = re.compile(r"\(([^)]+)\)\s*$")


def vscode_family_editor_dirs(tool_name: str) -> list:
    """User-data dir names for a VS Code-family Copilot row.

    Copilot runs in VS Code forks, and each keeps its own user data dir, so the
    row's editor decides where its prompts and MCP config live. An unnamed tool
    means the legacy union over every supported editor.
    """
    if not tool_name:
        return list(VSCODE_EDITOR_DISPLAY_NAMES)
    match = _ROW_EDITOR_SUFFIX.search(tool_name)
    if match is None:
        return []
    key = _EDITOR_KEY_BY_DISPLAY.get(match.group(1).strip().lower())
    return [key] if key else []


def extensions_dir_for_editor(user_home: Path, ide_key: str) -> Optional[Path]:
    """Return the extensions registry directory for ``ide_key`` under ``user_home``.

    Args:
        user_home: The user's home directory.
        ide_key: The editor key (``SUPPORTED_IDES`` folder name), e.g. ``"Code"``.

    Returns:
        The ``<user_home>/<rel>/extensions`` Path, or None for an unknown editor.
    """
    rel = _EXTENSIONS_DIR_BY_EDITOR.get(ide_key)
    if rel is None:
        return None
    return user_home / rel


def find_extension_in_editor(
    user_home: Path, ide_key: str, ext_id: str
) -> Optional[Tuple[str, Optional[str]]]:
    """Return ``(matched_location, version)`` if ``ext_id`` is a live entry in the
    editor's ``extensions.json``, else None.

    Matches case-insensitively on ``identifier.id`` (constants and registry entries
    disagree on casing, e.g. ``kilocode.Kilo-Code`` vs ``kilocode.kilo-code``).
    Never raises — returns None for an unknown editor or a missing/corrupt registry.

    Args:
        user_home: The user's home directory.
        ide_key: The editor key (``SUPPORTED_IDES`` folder name).
        ext_id: The extension identifier (e.g. ``saoudrizwan.claude-dev``).

    Returns:
        ``(matched_location, version)`` tuple, or None.
    """
    extension, _complete = find_extension_in_editor_with_status(
        user_home,
        ide_key,
        ext_id,
    )
    return extension


def find_extension_in_editor_with_status(
    user_home: Path, ide_key: str, ext_id: str
) -> Tuple[Optional[Tuple[str, Optional[str]]], bool]:
    extensions_dir = extensions_dir_for_editor(user_home, ide_key)
    if extensions_dir is None:
        return None, True

    registry = extensions_dir / "extensions.json"
    target = ext_id.lower()

    try:
        entries = json.loads(registry.read_text(encoding="utf-8-sig", errors="replace"))
    except FileNotFoundError:
        return None, False
    except OSError as exc:
        logger.debug(f"Could not read extensions registry {registry}: {exc}")
        return None, False
    except ValueError as exc:
        logger.debug(f"Could not decode extensions registry {registry}: {exc}")
        return None, False

    if not isinstance(entries, list):
        return None, False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("identifier")
        entry_id = identifier.get("id") if isinstance(identifier, dict) else None
        if not isinstance(entry_id, str) or entry_id.lower() != target:
            continue

        version = entry.get("version")
        version = version if isinstance(version, str) else None
        return (_resolve_entry_location(entry, extensions_dir), version), True

    return None, True


def _resolve_entry_location(entry: dict, extensions_dir: Path) -> str:
    """Resolve the on-disk location string for an ``extensions.json`` entry.

    Prefers native ``location.fsPath`` / ``location.path`` values. VS Code on
    Windows records ``location.path`` as a URI path (``/c:/...``), so that one
    shape falls back to ``relativeLocation``. Never raises.
    """
    location = entry.get("location")
    if isinstance(location, dict):
        fs_path = location.get("fsPath")
        if isinstance(fs_path, str) and fs_path:
            return fs_path
        uri_path = location.get("path")
        windows_uri_path = (
            isinstance(uri_path, str)
            and re.match(r"^/[A-Za-z]:[/\\]", uri_path) is not None
        )
        if isinstance(uri_path, str) and uri_path and not windows_uri_path:
            return uri_path

    rel_location = entry.get("relativeLocation")
    if isinstance(rel_location, str) and rel_location:
        return str(extensions_dir / rel_location)

    if isinstance(location, dict):
        uri_path = location.get("path")
        if isinstance(uri_path, str) and uri_path:
            return uri_path

    return str(extensions_dir)
