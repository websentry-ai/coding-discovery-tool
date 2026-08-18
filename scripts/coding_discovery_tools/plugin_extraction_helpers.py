"""
Shared OS-agnostic helper functions for plugin provenance detection.

Provides extraction of plugin metadata from Claude Code and Cursor plugin
directories, including marketplace source, blocklist status, and capability
detection. Uses functional composition — no classes.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .constants import is_symlink_or_junction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_json_file(file_path: Path) -> Optional[dict]:
    """Safely read and parse a JSON file, returning None on any error."""
    try:
        if not file_path.exists() or not file_path.is_file():
            return None
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return json.loads(content)
    except (json.JSONDecodeError, PermissionError, OSError) as exc:
        logger.debug("Error reading JSON file %s: %s", file_path, exc)
        return None
    except Exception as exc:
        logger.debug("Unexpected error reading %s: %s", file_path, exc)
        return None


def _build_blocklist_set(blocklist_data: Optional[dict]) -> set:
    """Build a set of blocked plugin_ids from blocklist.json data."""
    if not blocklist_data:
        return set()
    try:
        return {
            entry.get("plugin", "")
            for entry in blocklist_data.get("plugins", [])
            if isinstance(entry, dict) and entry.get("plugin")
        }
    except Exception as exc:
        logger.debug("Error parsing blocklist: %s", exc)
        return set()


def _build_blocklist_reasons(blocklist_data: Optional[dict]) -> Dict[str, str]:
    """Build a mapping of plugin_id -> block reason from blocklist.json data."""
    if not blocklist_data:
        return {}
    try:
        return {
            entry["plugin"]: entry.get("reason", "")
            for entry in blocklist_data.get("plugins", [])
            if isinstance(entry, dict) and entry.get("plugin")
        }
    except Exception as exc:
        logger.debug("Error parsing blocklist reasons: %s", exc)
        return {}


_OFFICIAL_CLAUDE_MARKETPLACES = frozenset({
    "claude-plugins-official",
})


def _is_official_claude_marketplace(marketplace_name: str) -> bool:
    """Check if a marketplace is the official Claude Code marketplace."""
    return marketplace_name.lower() in _OFFICIAL_CLAUDE_MARKETPLACES


_OFFICIAL_CURSOR_MARKETPLACES = frozenset({
    "cursor-plugins-official",
    "cursor-official",
})


def _is_official_cursor_marketplace(marketplace_name: str) -> bool:
    """Check if a marketplace is an official Cursor marketplace."""
    return marketplace_name.lower() in _OFFICIAL_CURSOR_MARKETPLACES


_OFFICIAL_CODEX_MARKETPLACES = frozenset({
    "openai-curated",
})


def _is_official_codex_marketplace(marketplace_name: str) -> bool:
    """Check if a marketplace is the official Codex (OpenAI) marketplace."""
    return marketplace_name.lower() in _OFFICIAL_CODEX_MARKETPLACES


# Header tolerates surrounding whitespace: `[ plugins."id" ]`. The enabled value is
# captured up to whitespace/`#`, so a trailing comment (`enabled = false  # off`) and
# case (`False`) are handled.
_CODEX_PLUGIN_HEADER_RE = re.compile(r'^\s*\[\s*plugins\.\s*"([^"]+)"\s*\]\s*$')
_CODEX_ENABLED_RE = re.compile(r'^\s*enabled\s*=\s*([^\s#]+)')


def _codex_disabled_plugin_ids(codex_home: Path) -> set:
    """Set of ``<plugin>@<marketplace>`` ids EXPLICITLY disabled in ``~/.codex/config.toml``.

    Codex treats an installed plugin as **enabled unless it is turned off**, so callers
    skip only these ids — a missing/unreadable config, a plugin with no table, or an
    unparseable ``enabled`` value all fail OPEN (the plugin is reported). Parsed with a
    tiny line scanner rather than a TOML library on purpose (``tomllib`` is 3.11+; this
    codebase targets 3.9); comment-, whitespace- and case-tolerant, and multiline-string
    aware so a fake record embedded in string content can't disable a real plugin.
    """
    disabled: set = set()
    config = codex_home / "config.toml"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return disabled  # fail open -> caller reports the plugin
    current: Optional[str] = None
    in_multiline: Optional[str] = None  # the ''' or \"\"\" delimiter we are inside, if any
    for line in text.splitlines():
        if in_multiline:
            if line.count(in_multiline) % 2 == 1:
                in_multiline = None
            continue
        for delim in ('"""', "'''"):
            if line.count(delim) % 2 == 1:
                in_multiline = delim
                break
        if in_multiline:
            continue
        header = _CODEX_PLUGIN_HEADER_RE.match(line)
        if header:
            current = header.group(1)
            continue
        if line.lstrip().startswith("["):          # any other table ends the block
            current = None
            continue
        if current:
            m = _CODEX_ENABLED_RE.match(line)
            if m and m.group(1).strip().strip('"').lower() == "false":
                disabled.add(current)
    return disabled


def _codex_plugin_version_key(hash_dir: Path):
    """Sort key for choosing a plugin's active install dir: (version_tuple, mtime).

    Codex resolves the active install by highest ``version`` (from the plugin's
    ``.codex-plugin/plugin.json``), so we prefer that; ``mtime`` only breaks ties or
    fills in when the manifest has no parseable version. Best-effort — never raises.
    """
    version_tuple = (-1,)
    try:
        manifest = _read_json_file(hash_dir / ".codex-plugin" / "plugin.json") or {}
        raw = str(manifest.get("version", ""))
        parts = tuple(int(p) for p in re.split(r"[.\-+]", raw) if p.isdigit())
        if parts:
            version_tuple = parts
    except Exception:  # pragma: no cover - version parsing is advisory only
        pass
    try:
        mtime = hash_dir.stat().st_mtime
    except (OSError, PermissionError):
        mtime = 0.0
    return (version_tuple, mtime)


def _is_within(child: Path, parent: Path) -> bool:
    """True if `child` resolves to a path inside `parent` (symlink-escape guard)."""
    try:
        child_real = Path(os.path.realpath(str(child)))
        parent_real = Path(os.path.realpath(str(parent)))
        return child_real == parent_real or parent_real in child_real.parents
    except (OSError, ValueError):
        return False


def extract_codex_plugins(codex_home: Path) -> List[Dict]:
    """Enumerate installed Codex marketplace plugins.

    Codex installs plugins (``codex plugin add <plugin>@<marketplace>``) under
    ``<codex_home>/plugins/cache/<marketplace>/<plugin>/<hash>/`` and each plugin can
    bundle skills at ``<hash>/skills/<name>/SKILL.md`` — the Codex analogue of the
    plugin-bundled skills already reported for Claude Code and Cursor. Returns dicts in
    the shape :func:`extract_plugin_skills` consumes. Disabled plugins (per
    ``config.toml``) are skipped.

    Robust by construction: a filesystem error in one marketplace/plugin skips only that
    entry and keeps scanning the rest, symlinked cache entries are rejected, and every
    chosen install path is confirmed to resolve inside ``codex_home`` so a crafted cache
    layout cannot redirect reads outside the owning user's tree.
    """
    plugins: List[Dict] = []
    cache = codex_home / "plugins" / "cache"
    if not cache.is_dir() or is_symlink_or_junction(cache):
        return plugins

    disabled_ids = _codex_disabled_plugin_ids(codex_home)  # Codex: enabled unless turned off
    try:
        marketplaces = list(cache.iterdir())
    except (OSError, PermissionError) as exc:
        logger.debug("Error listing Codex plugin cache %s: %s", cache, exc)
        return plugins

    for marketplace_dir in marketplaces:
        try:
            if not marketplace_dir.is_dir() or is_symlink_or_junction(marketplace_dir):
                continue
            marketplace_name = marketplace_dir.name
            for plugin_dir in marketplace_dir.iterdir():
                try:
                    if not plugin_dir.is_dir() or is_symlink_or_junction(plugin_dir):
                        continue
                    plugin_name = plugin_dir.name
                    plugin_id = f"{plugin_name}@{marketplace_name}"
                    if plugin_id in disabled_ids:
                        continue  # explicitly disabled in config.toml

                    # Multiple content-hash install dirs can accumulate after updates;
                    # pick the one Codex would load — highest version, mtime as tiebreak —
                    # among the non-symlinked dirs that actually carry a skills/ payload.
                    candidates = [
                        d for d in plugin_dir.iterdir()
                        if d.is_dir() and not is_symlink_or_junction(d)
                        and (d / "skills").is_dir()
                    ]
                    if not candidates:
                        continue
                    install_path = max(candidates, key=_codex_plugin_version_key)

                    # Containment guard: the chosen dir must resolve inside codex_home.
                    if not _is_within(install_path, codex_home):
                        logger.debug("Skipping Codex plugin dir outside home: %s", install_path)
                        continue

                    plugins.append({
                        "plugin_id": plugin_id,
                        "plugin_name": plugin_name,
                        "marketplace_name": marketplace_name,
                        "install_path": str(install_path),
                        "has_skills": True,
                        "source_type": "marketplace",
                        "is_official": _is_official_codex_marketplace(marketplace_name),
                    })
                except (OSError, PermissionError) as exc:
                    logger.debug("Skipping unreadable Codex plugin %s: %s", plugin_dir, exc)
                    continue
        except (OSError, PermissionError) as exc:
            logger.debug("Skipping unreadable Codex marketplace %s: %s", marketplace_dir, exc)
            continue
    return plugins


def _construct_source_url(source_type: str, source_repo: Optional[str]) -> Optional[str]:
    """Construct a URL from source type and repo identifier."""
    if source_type == "github" and source_repo:
        # Validate repo format (owner/repo) to prevent URL injection from untrusted JSON
        if "/" in source_repo and all(
            c.isalnum() or c in "-_./+" for c in source_repo
        ):
            return f"https://github.com/{source_repo}"
    return None


def _detect_plugin_capabilities(install_path: Path, manifest: Optional[dict]) -> Dict[str, bool]:
    """Detect plugin capabilities from both disk contents and manifest."""
    manifest = manifest or {}

    has_skills = False
    has_hooks = False
    has_mcp_servers = False
    has_agents = False
    has_commands = False

    # Check disk contents
    try:
        has_skills = (install_path / "skills").is_dir()
    except (PermissionError, OSError):
        pass
    try:
        has_hooks = (install_path / "hooks").is_dir()
    except (PermissionError, OSError):
        pass
    try:
        has_mcp_servers = (install_path / ".mcp.json").is_file()
    except (PermissionError, OSError):
        pass

    # Check manifest
    if not has_mcp_servers and manifest.get("mcpServers"):
        has_mcp_servers = True

    agents_list = manifest.get("agents", [])
    has_agents = isinstance(agents_list, list) and len(agents_list) > 0

    commands_list = manifest.get("commands", [])
    has_commands = isinstance(commands_list, list) and len(commands_list) > 0

    return {
        "has_skills": has_skills,
        "has_hooks": has_hooks,
        "has_mcp_servers": has_mcp_servers,
        "has_agents": has_agents,
        "has_commands": has_commands,
    }


def _extract_manifest_fields(manifest: Optional[dict]) -> dict:
    """Extract common fields from a plugin.json manifest."""
    if not manifest:
        return {
            "author_name": None,
            "homepage": None,
            "license": None,
        }

    author = manifest.get("author")
    author_name = None
    if isinstance(author, dict):
        author_name = author.get("name")
    elif isinstance(author, str):
        author_name = author

    return {
        "author_name": author_name,
        "homepage": manifest.get("homepage"),
        "license": manifest.get("license"),
    }


# ---------------------------------------------------------------------------
# Claude Code plugin extraction
# ---------------------------------------------------------------------------

def extract_claude_code_plugins(plugins_dir: Path) -> List[Dict]:
    """
    Extract plugin metadata from a Claude Code plugins directory.

    Reads installed_plugins.json (v2 format), cross-references
    known_marketplaces.json and blocklist.json, and reads each plugin's
    .claude-plugin/plugin.json manifest for detailed metadata.

    Args:
        plugins_dir: Path to ~/.claude/plugins

    Returns:
        List of plugin dicts with provenance and capability metadata.
        Returns empty list if directory is missing or data is malformed.
    """
    if not plugins_dir or not plugins_dir.is_dir():
        return []

    installed_data = _read_json_file(plugins_dir / "installed_plugins.json")
    if not installed_data:
        return []

    # The `version` field is read but not gated on: the plugins map has been
    # {plugin_id: [entries]} throughout, so bailing on an unrecognised version
    # would silently drop every plugin rather than degrade.
    version = installed_data.get("version")
    if version != 2:
        logger.warning("Unexpected installed_plugins.json version: %s (expected 2); reading anyway", version)

    plugins_map = installed_data.get("plugins", {})
    if not isinstance(plugins_map, dict):
        return []

    marketplaces_data = _read_json_file(plugins_dir / "known_marketplaces.json") or {}
    blocklist_data = _read_json_file(plugins_dir / "blocklist.json")
    blocked_set = _build_blocklist_set(blocklist_data)
    block_reasons = _build_blocklist_reasons(blocklist_data)

    results: List[Dict] = []

    for plugin_id, install_entries in plugins_map.items():
        if not isinstance(install_entries, list):
            continue

        for entry in install_entries:
            try:
                result = _process_claude_code_plugin_entry(
                    plugin_id, entry, marketplaces_data, blocked_set, block_reasons
                )
                if result:
                    results.append(result)
            except Exception as exc:
                logger.debug("Error processing plugin %s: %s", plugin_id, exc)

    return results


def _process_claude_code_plugin_entry(
    plugin_id: str,
    entry: dict,
    marketplaces_data: dict,
    blocked_set: set,
    block_reasons: Dict[str, str],
) -> Optional[Dict]:
    """Process a single Claude Code plugin installation entry."""
    if not isinstance(entry, dict):
        return None

    # Parse plugin_id: "name@marketplace"
    parts = plugin_id.split("@", 1)
    plugin_name = parts[0] if parts else plugin_id
    marketplace_name = parts[1] if len(parts) > 1 else ""

    install_path_str = entry.get("installPath", "")
    install_path = Path(install_path_str) if install_path_str else None

    # Marketplace metadata
    marketplace_info = marketplaces_data.get(marketplace_name, {})
    marketplace_source = marketplace_info.get("source", {}) if isinstance(marketplace_info, dict) else {}
    marketplace_source_type = marketplace_source.get("source", "") if isinstance(marketplace_source, dict) else ""
    marketplace_repo = marketplace_source.get("repo") if isinstance(marketplace_source, dict) else None

    source_url = _construct_source_url(marketplace_source_type, marketplace_repo)
    is_official = _is_official_claude_marketplace(marketplace_name)

    # Blocklist check
    is_blocked = plugin_id in blocked_set
    block_reason = block_reasons.get(plugin_id) if is_blocked else None

    # Read manifest from install path
    manifest = None
    capabilities = {
        "has_skills": False,
        "has_hooks": False,
        "has_mcp_servers": False,
        "has_agents": False,
        "has_commands": False,
    }
    manifest_fields = _extract_manifest_fields(None)

    if install_path and install_path.is_dir():
        manifest_path = install_path / ".claude-plugin" / "plugin.json"
        manifest = _read_json_file(manifest_path)
        capabilities = _detect_plugin_capabilities(install_path, manifest)
        manifest_fields = _extract_manifest_fields(manifest)

    return {
        "plugin_name": plugin_name,
        "plugin_id": plugin_id,
        "marketplace_name": marketplace_name,
        "version": entry.get("version"),
        "scope": entry.get("scope"),
        "enabled": True,
        "blocked": is_blocked,
        "block_reason": block_reason,
        "installed_at": entry.get("installedAt"),
        "git_commit_sha": entry.get("gitCommitSha"),
        # source_type mirrors marketplace_source_type today; kept as a
        # top-level convenience field used by provenance tagging on skills/MCP.
        "source_type": marketplace_source_type or None,
        "source_url": source_url,
        "source_repo": marketplace_repo,
        "marketplace_source_type": marketplace_source_type or None,
        "marketplace_repo": marketplace_repo,
        "is_official": is_official,
        "install_path": install_path_str or None,
        **manifest_fields,
        **capabilities,
    }


# ---------------------------------------------------------------------------
# Cursor plugin extraction
# ---------------------------------------------------------------------------

def extract_cursor_plugins(plugins_dir: Path) -> List[Dict]:
    """
    Extract plugin metadata from a Cursor plugins directory.

    Walks cache/<marketplace>/<plugin>/<sha>/ directories, reading
    .cursor-plugin/plugin.json and .cursor-plugin/marketplace.json
    for metadata.

    Args:
        plugins_dir: Path to the Cursor plugins directory

    Returns:
        List of plugin dicts with provenance and capability metadata.
        Returns empty list if directory is missing or empty.
    """
    if not plugins_dir or not plugins_dir.is_dir():
        return []

    cache_dir = plugins_dir / "cache"
    if not cache_dir.is_dir():
        return []

    results: List[Dict] = []

    try:
        for marketplace_dir in cache_dir.iterdir():
            if not marketplace_dir.is_dir():
                continue
            try:
                for plugin_dir in marketplace_dir.iterdir():
                    if not plugin_dir.is_dir():
                        continue
                    try:
                        for version_dir in plugin_dir.iterdir():
                            if not version_dir.is_dir():
                                continue
                            result = _process_cursor_plugin_dir(
                                version_dir, marketplace_dir.name, plugin_dir.name
                            )
                            if result:
                                results.append(result)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError) as exc:
        logger.debug("Error scanning Cursor plugin cache %s: %s", cache_dir, exc)
    except Exception as exc:
        logger.debug("Unexpected error scanning Cursor plugins: %s", exc)

    return results


def _process_cursor_plugin_dir(
    version_dir: Path,
    marketplace_name: str,
    plugin_dir_name: str,
) -> Optional[Dict]:
    """Process a single Cursor plugin version directory."""
    try:
        cursor_plugin_dir = version_dir / ".cursor-plugin"

        manifest = None
        marketplace_data = None

        if cursor_plugin_dir.is_dir():
            manifest = _read_json_file(cursor_plugin_dir / "plugin.json")
            marketplace_data = _read_json_file(cursor_plugin_dir / "marketplace.json")

        plugin_name = plugin_dir_name
        if manifest and manifest.get("name"):
            plugin_name = manifest["name"]

        plugin_id = f"{plugin_dir_name}@{marketplace_name}"
        is_official = _is_official_cursor_marketplace(marketplace_name)

        # Extract marketplace source info
        marketplace_source_type = None
        marketplace_repo = None
        source_url = None

        if marketplace_data:
            source_info = marketplace_data.get("source", {})
            if isinstance(source_info, dict):
                marketplace_source_type = source_info.get("source")
                marketplace_repo = source_info.get("repo")
                source_url = _construct_source_url(
                    marketplace_source_type or "", marketplace_repo
                )

        capabilities = _detect_plugin_capabilities(version_dir, manifest)
        manifest_fields = _extract_manifest_fields(manifest)

        version = version_dir.name
        if manifest and manifest.get("version"):
            version = manifest["version"]

        return {
            "plugin_name": plugin_name,
            "plugin_id": plugin_id,
            "marketplace_name": marketplace_name,
            "version": version,
            "scope": None,
            "enabled": True,
            "blocked": False,
            "block_reason": None,
            "installed_at": None,
            "git_commit_sha": version_dir.name if version_dir.name != version else None,
            "source_type": marketplace_source_type,
            "source_url": source_url,
            "source_repo": marketplace_repo,
            "marketplace_source_type": marketplace_source_type,
            "marketplace_repo": marketplace_repo,
            "is_official": is_official,
            "install_path": str(version_dir),
            **manifest_fields,
            **capabilities,
        }
    except Exception as exc:
        logger.debug("Error processing Cursor plugin dir %s: %s", version_dir, exc)
        return None


# ---------------------------------------------------------------------------
# Plugin install-path lookup builder
# ---------------------------------------------------------------------------

def build_plugin_install_path_lookup(plugins: List[Dict]) -> Dict[str, Dict]:
    """
    Build a dict keyed by install_path for O(1) lookup when tagging skills/MCP servers.

    Each value contains the provenance fields needed for tagging:
    plugin_id, marketplace_name, source_type, is_official.

    Args:
        plugins: List of plugin dicts (from extract_claude_code_plugins or
                 extract_cursor_plugins)

    Returns:
        Dict mapping install_path string to provenance metadata dict.
        Paths without an install_path are skipped.
    """
    lookup: Dict[str, Dict] = {}
    for plugin in plugins:
        install_path = plugin.get("install_path")
        if not install_path:
            continue
        lookup[install_path] = {
            "plugin_id": plugin.get("plugin_id"),
            "marketplace_name": plugin.get("marketplace_name"),
            "source_type": plugin.get("source_type"),
            "is_official": plugin.get("is_official", False),
        }
    return lookup


def find_plugin_provenance_by_path(
    path_str: str,
    plugin_lookup: Optional[Dict[str, Dict]],
) -> Optional[Dict]:
    """
    Find plugin provenance for a path using longest-prefix match.

    Args:
        path_str: Absolute path string to check
        plugin_lookup: Dict mapping plugin install_path to provenance metadata

    Returns:
        Dict with plugin_id, marketplace_name, source_type, is_official
        or None if no match found.
    """
    if not plugin_lookup or not path_str:
        return None

    best_match_path = ""
    best_match_info = None
    normalized_path = path_str.replace("\\", "/").rstrip("/")
    for install_path, info in plugin_lookup.items():
        normalized_install = install_path.replace("\\", "/").rstrip("/")
        if (normalized_path == normalized_install or normalized_path.startswith(normalized_install + "/")) \
                and len(normalized_install) > len(best_match_path):
            best_match_path = normalized_install
            best_match_info = info

    if best_match_info:
        return {
            "plugin_id": best_match_info.get("plugin_id"),
            "marketplace_name": best_match_info.get("marketplace_name"),
            "source_type": best_match_info.get("source_type"),
            "is_official": best_match_info.get("is_official", False),
        }
    return None


# ---------------------------------------------------------------------------
# Plugin-bundled skills extraction
# ---------------------------------------------------------------------------

MAX_SKILL_FILE_SIZE = 50 * 1024  # 50KB


def extract_plugin_skills(plugins: List[Dict]) -> List[Dict]:
    """
    Extract skills bundled inside plugin install paths.

    Walks <install_path>/skills/<name>/SKILL.md for each plugin that has
    has_skills=True. Returns skill dicts tagged with source="plugin" and
    provenance metadata, matching the format of the standalone skills pipeline.

    Args:
        plugins: List of plugin dicts from extract_claude_code_plugins or
                 extract_cursor_plugins.

    Returns:
        List of skill dicts ready to merge into user_skills.
    """
    from .claude_code_skills_helpers import _is_foreign_hardlink  # local: avoid import cycle
    skills: List[Dict] = []
    for plugin in plugins:
        install_path = plugin.get("install_path")
        if not install_path or not plugin.get("has_skills"):
            continue
        skills_dir = Path(install_path) / "skills"
        try:
            if not skills_dir.is_dir() or is_symlink_or_junction(skills_dir):
                continue
            for entry in skills_dir.iterdir():
                # Reject symlinked entries and a symlinked SKILL.md before reading: a
                # crafted plugin could point skills/<name>/SKILL.md at another file
                # (e.g. ~/.ssh/id_ed25519) even within the same home, and is_file()
                # follows the link. Rejecting the link means we never read the target.
                if not entry.is_dir() or is_symlink_or_junction(entry):
                    continue
                skill_file = entry / "SKILL.md"
                # Reject symlinks (leaf may point elsewhere) and foreign hardlinks
                # (st_nlink > 1 -> a second link could be another user's file) before
                # reading, so a crafted plugin can't smuggle off-plugin content in.
                if (not skill_file.is_file()
                        or is_symlink_or_junction(skill_file)
                        or _is_foreign_hardlink(skill_file)):
                    continue
                try:
                    st = skill_file.stat()
                    file_size = st.st_size
                    raw = skill_file.read_text(encoding="utf-8", errors="replace")
                    raw_bytes = raw.encode("utf-8")
                    truncated = len(raw_bytes) > MAX_SKILL_FILE_SIZE
                    content = raw_bytes[:MAX_SKILL_FILE_SIZE].decode("utf-8", errors="ignore") if truncated else raw
                    last_modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()

                    skills.append({
                        "file_path": str(skill_file),
                        "file_name": "SKILL.md",
                        "content": content,
                        "size": file_size,
                        "last_modified": last_modified,
                        "truncated": truncated,
                        "scope": "user",
                        "skill_name": entry.name,
                        "type": "skill",
                        "source": "plugin",
                        "plugin_id": plugin.get("plugin_id"),
                        "marketplace_name": plugin.get("marketplace_name"),
                        "source_type": plugin.get("source_type"),
                        "is_official": plugin.get("is_official", False),
                    })
                except (PermissionError, OSError, UnicodeDecodeError):
                    continue
        except (PermissionError, OSError):
            continue
    return skills
