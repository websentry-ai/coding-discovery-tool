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
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Per-user extensions dir per VS Code-family editor, keyed by SUPPORTED_IDES folder
# name. Home-relative and identical on all OSes (VS Code uses ~/.vscode/extensions
# cross-platform, not the ~/.config app-data tree).
_EXTENSIONS_DIR_BY_EDITOR = {
    "Code": ".vscode/extensions",
    "Cursor": ".cursor/extensions",
    "Windsurf": ".windsurf/extensions",
    "VSCodium": ".vscode-oss/extensions",
    "Antigravity": ".antigravity/extensions",
}


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
    extensions_dir = extensions_dir_for_editor(user_home, ide_key)
    if extensions_dir is None:
        return None

    registry = extensions_dir / "extensions.json"
    target = ext_id.lower()

    try:
        if not registry.is_file():
            return None
        entries = json.loads(registry.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.debug(f"Could not read extensions registry {registry}: {exc}")
        return None

    if not isinstance(entries, list):
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("identifier")
        entry_id = identifier.get("id") if isinstance(identifier, dict) else None
        if not isinstance(entry_id, str) or entry_id.lower() != target:
            continue

        version = entry.get("version")
        version = version if isinstance(version, str) else None
        return _resolve_entry_location(entry, extensions_dir), version

    return None


def _resolve_entry_location(entry: dict, extensions_dir: Path) -> str:
    """Resolve the on-disk location string for an ``extensions.json`` entry.

    Prefers the absolute ``location.path``/``location.fsPath`` recorded by the
    editor, then a ``relativeLocation`` resolved under the extensions dir, and
    finally the extensions dir itself. Never raises.
    """
    location = entry.get("location")
    if isinstance(location, dict):
        abs_path = location.get("path") or location.get("fsPath")
        if isinstance(abs_path, str) and abs_path:
            return abs_path

    rel_location = entry.get("relativeLocation")
    if isinstance(rel_location, str) and rel_location:
        return str(extensions_dir / rel_location)

    return str(extensions_dir)
