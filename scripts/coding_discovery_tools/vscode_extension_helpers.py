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
import os
import re
import stat
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

# A renamed data folder is the same editor, not a new one -- kept out of the map
# above so it keeps one key, one display name and one row. The renamed dir is the
# one the current build reads; the one in the map above is the fallback.
_EXTENSIONS_DIR_ALTERNATES = {
    "Windsurf": (".devin/extensions",),
}

# For callers that scan every editor rather than a fixed SUPPORTED_IDES subset.
VSCODE_EDITOR_KEYS = tuple(_EXTENSIONS_DIR_BY_EDITOR)

# Row label per user-data dir name. Insiders is folded into ``Code`` downstream.
VSCODE_EDITOR_DISPLAY_NAMES = {
    "Code": "VS Code",
    "Cursor": "Cursor",
    "Windsurf": "Devin Desktop",
    "VSCodium": "VSCodium",
    "Antigravity": "Antigravity",
}

# Names an editor used to ship under. A row written before the rename still reads
# "(Windsurf)", and it has to resolve to the same editor as one written after it.
_FORMER_DISPLAY_NAMES = {"Windsurf": "Windsurf"}

_EDITOR_KEY_BY_DISPLAY = {
    display.lower(): key
    for mapping in (VSCODE_EDITOR_DISPLAY_NAMES, _FORMER_DISPLAY_NAMES)
    for key, display in mapping.items()
}
_ROW_EDITOR_SUFFIX = re.compile(r"\(([^)]+)\)\s*$")

# Insiders is a VS Code channel, not its own row, so it is searched under ``Code``.
_EXTENSION_DIR_KEYS_BY_EDITOR = {"Code": ("Code", "Code - Insiders")}

# Registry read outcomes seen this run, as ``<editor>:<outcome>``. A zero-tool scan
# cannot otherwise act on a None from find_extension_in_editor: a registry that is
# missing, one we could not read, and one that simply does not list Copilot are
# three different bugs and one return value.
_REGISTRY_OUTCOMES_CAP = 12
_registry_outcomes = set()


def _record_registry_outcome(ide_key: str, outcome: str) -> None:
    """Note how one editor's ``extensions.json`` read ended. Never raises."""
    try:
        if len(_registry_outcomes) < _REGISTRY_OUTCOMES_CAP:
            _registry_outcomes.add(f"{ide_key}:{outcome}")
    except Exception:
        pass


def vscode_registry_state() -> list:
    """This run's ``extensions.json`` outcomes: missing, unreadable, present, listed."""
    return sorted(_registry_outcomes)


def reset_vscode_registry_state() -> None:
    """Clear the per-run registry outcomes."""
    _registry_outcomes.clear()


def find_extension_in_editor_channels(
    user_home: Path, ide_key: str, ext_id: str
) -> Optional[Tuple[str, Optional[str]]]:
    """``(dir_key, version)`` for the first channel of ``ide_key`` listing ``ext_id``,
    stable before Insiders. None when no channel lists it. Never raises."""
    for dir_key in _EXTENSION_DIR_KEYS_BY_EDITOR.get(ide_key, (ide_key,)):
        entry = find_extension_in_editor(user_home, dir_key, ext_id)
        if entry is not None:
            return dir_key, entry[1]
    return None


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


def _extension_dir_candidates(user_home: Path, ide_key: str) -> list:
    """This editor's extensions dirs, live one first; [] for an unknown editor."""
    rel = _EXTENSIONS_DIR_BY_EDITOR.get(ide_key)
    if rel is None:
        return []
    renamed = _EXTENSIONS_DIR_ALTERNATES.get(ide_key, ())
    return [user_home / alt for alt in renamed] + [user_home / rel]


def _holds_registry(extensions_dir: Path) -> bool:
    """Whether this dir holds an ``extensions.json`` file. Never raises."""
    try:
        return stat.S_ISREG(os.stat(extensions_dir / "extensions.json").st_mode)
    except OSError:
        return False


def extensions_dir_for_editor(user_home: Path, ide_key: str) -> Optional[Path]:
    """Return the extensions registry directory for ``ide_key`` under ``user_home``.

    A migrated machine keeps the editor's pre-rename dir beside the current one, and
    the dir holding a registry is the one the installed build reads.

    Args:
        user_home: The user's home directory.
        ide_key: The editor key (``SUPPORTED_IDES`` folder name), e.g. ``"Code"``.

    Returns:
        The ``<user_home>/<rel>/extensions`` Path, or None for an unknown editor.
    """
    candidates = _extension_dir_candidates(user_home, ide_key)
    if not candidates:
        return None
    for candidate in candidates:
        if _holds_registry(candidate):
            return candidate
    for candidate in candidates:
        try:                            # an unreadable home must not hide the rest
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[-1]


# A dir with a readable registry has answered for its editor, hit or miss; the other
# is the pre-rename leftover and must not answer in its place.
_ANSWERED = ("listed", "present")


def find_extension_in_editor(
    user_home: Path, ide_key: str, ext_id: str
) -> Optional[Tuple[str, Optional[str]]]:
    """Return ``(matched_location, version)`` if ``ext_id`` is a live entry in any of
    the editor's ``extensions.json`` registries, else None.

    Matches case-insensitively on ``identifier.id`` (constants and registry entries
    disagree on casing, e.g. ``kilocode.Kilo-Code`` vs ``kilocode.kilo-code``). A
    migrated machine keeps the old data folder beside the new one, so the first
    registry that can be read answers and the leftover is only a fallback.
    Never raises — returns None for an unknown editor or a missing/corrupt registry.

    Args:
        user_home: The user's home directory.
        ide_key: The editor key (``SUPPORTED_IDES`` folder name).
        ext_id: The extension identifier (e.g. ``saoudrizwan.claude-dev``).

    Returns:
        ``(matched_location, version)`` tuple, or None.
    """
    outcome, result = None, None
    for extensions_dir in _extension_dir_candidates(user_home, ide_key):
        read, match = _lookup_in_registry(extensions_dir, ext_id)
        if read in _ANSWERED:
            outcome, result = read, match
            break
        if outcome != "unreadable":     # a denied dir is more telling than an absent one
            outcome = read
    if outcome is not None:
        _record_registry_outcome(ide_key, outcome)
    return result


def _lookup_in_registry(
    extensions_dir: Path, ext_id: str
) -> Tuple[str, Optional[Tuple[str, Optional[str]]]]:
    """Look ``ext_id`` up in one extensions dir: ``(outcome, match or None)``."""
    registry = extensions_dir / "extensions.json"

    # os.stat, not is_file(): 3.14 returns False for an unreadable path, which would
    # report a registry we were denied as one that is not there.
    try:
        mode = os.stat(registry).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "missing", None
    except OSError as exc:
        logger.debug(f"Could not stat extensions registry {registry}: {exc}")
        return "unreadable", None
    if not stat.S_ISREG(mode):
        return "missing", None

    try:
        entries = json.loads(registry.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.debug(f"Could not read extensions registry {registry}: {exc}")
        return "unreadable", None

    if not isinstance(entries, list):
        return "unreadable", None

    entry = _matching_entry(entries, ext_id.lower())
    if entry is None:
        return "present", None

    version = entry.get("version")
    version = version if isinstance(version, str) else None
    return "listed", (_resolve_entry_location(entry, extensions_dir), version)


def _matching_entry(entries: list, target: str) -> Optional[dict]:
    """The first entry whose ``identifier.id`` equals ``target``, or None."""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("identifier")
        entry_id = identifier.get("id") if isinstance(identifier, dict) else None
        if isinstance(entry_id, str) and entry_id.lower() == target:
            return entry
    return None


def _resolve_entry_location(entry: dict, extensions_dir: Path) -> str:
    """Resolve the on-disk location string for an ``extensions.json`` entry.

    Prefers the absolute ``location.path``/``location.fsPath`` recorded by the
    editor, then a ``relativeLocation`` resolved under the extensions dir, and
    finally the extensions dir itself. Never raises.
    """
    location = entry.get("location")
    if isinstance(location, dict):
        abs_path = location.get("fsPath") or location.get("path")
        if isinstance(abs_path, str) and abs_path:
            return abs_path

    rel_location = entry.get("relativeLocation")
    if isinstance(rel_location, str) and rel_location:
        return str(extensions_dir / rel_location)

    return str(extensions_dir)
