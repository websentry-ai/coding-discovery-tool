"""
Local per-device cache of MCP tool content hashes (``mcp-tools-cache.json``).

Written by the discovery run (after each per-(tool, user) report is built) and
by the single-server scan path. Read on the PreToolUse hot path by the setup
hooks, which look up
``tools[<coding_tool>][<home_user>][<cache_key>][<tool_name>]`` and relay the
content hash to the gateway for risk scoring.

File shape::

    {
      "updated_at": "<ISO8601Z>",
      "tools": {
        "<coding_tool_name>": {
          "<home_user>": {
            "<cache_key>": {
              "<tool_name>": "<content_hash>" | ["<content_hash>", ...], ...
            }
          }
        }
      }
    }

Lives in the same resolved state dir as discovery-cache.json
(``cache.UNBOUND_DIR`` — home ``~/.unbound`` or the temp fallback; never
hardcoded). Writes are atomic (mkstemp + os.replace) with the same
fallback-dir symlink guard as ``cache.atomic_write_cache``. Corrupt/missing
file on read is treated as ``{}``.
"""
import json
import logging
import os
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from . import cache as _state
from .content_hash import compute_tool_content_hash
from .mcp_fingerprint import compute_fingerprint
from .utils import report_to_sentry

logger = logging.getLogger(__name__)

MCP_TOOLS_CACHE_FILENAME = "mcp-tools-cache.json"


def _cache_path():
    # Read through the cache module at call time: _ensure_state_dir() may have
    # reassigned UNBOUND_DIR to the temp fallback after import.
    return _state.UNBOUND_DIR / MCP_TOOLS_CACHE_FILENAME


# ---------------------------------------------------------------------------
# <cache_key> — canonical MCP server fingerprint
#
# ⚠️ KEEP IN SYNC: this keying is duplicated in all five setup hooks —
#   setup/claude-code/hooks/unbound.py
#   setup/codex/hooks/unbound.py
#   setup/augment/hooks/unbound.py
#   setup/copilot/hooks/unbound.py
#   setup/cursor/unbound.py
# The hooks compute the same key at PreToolUse time to look entries up in the
# cache written here, so fingerprint output MUST stay identical across all
# implementations. Reviewers / the code-review bot: flag PRs that touch one
# without the others.
#
# The backend owns the canonical fingerprint rules. This module and the setup
# hooks carry stdlib-only ports so discovery and PreToolUse resolve the same
# server identity without retaining credentials or query parameters.
# ---------------------------------------------------------------------------

def compute_cache_key(name: Optional[str], url: Optional[str], command: Optional[str],
                      args: Optional[List], additional_data=None,
                      script_hash=None) -> Optional[str]:
    """Canonical fingerprint used as the local tool-cache key."""
    if name is not None and not isinstance(name, str):
        return None
    if url is not None and not isinstance(url, str):
        return None
    if command is not None and not isinstance(command, str):
        return None
    if args is not None and (
        not isinstance(args, list)
        or any(not isinstance(arg, str) for arg in args)
    ):
        return None
    if additional_data is not None and not isinstance(additional_data, dict):
        return None
    return compute_fingerprint(
        name=name,
        command=command,
        url=url,
        args=args,
        additional_data=additional_data,
        script_hash=script_hash,
    )


def cache_key_for_server(server: Dict) -> Optional[str]:
    """Cache key for one server object as produced by
    transform_mcp_servers_to_array (env/headers already stripped).
    Never raises; None when the server isn't cacheable."""
    try:
        return compute_cache_key(
            name=server.get("name"),
            url=server.get("url"),
            command=server.get("command"),
            args=server.get("args"),
            additional_data=server.get("additional_data"),
            script_hash=server.get("scriptHash"),
        )
    except Exception as e:
        logger.debug(f"cache key computation failed for server {server.get('name')!r}: {e}")
        return None


def tool_hashes_from_scan(scan: Optional[Dict]) -> Optional[Dict[str, str]]:
    """{tool_name: content_hash} from a server's `scan` block, or None when the
    scan errored / produced no tools."""
    if not isinstance(scan, dict) or scan.get("error") is not None:
        return None
    tools = scan.get("tools")
    if not isinstance(tools, list):
        return None
    hashes: Dict[str, str] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if isinstance(name, str) and name:
            hashes[name] = compute_tool_content_hash(tool)
    return hashes or None


def collect_server_entries(projects: Optional[List[Dict]]) -> Tuple[Dict[str, Dict], Set[str]]:
    """Derive cache entries from a report's ``projects[].mcpServers[]``.

    Returns ``(server_entries, errored_cache_keys)``:
      - server_entries: {cache_key: {tool_name: content_hash}} for servers
        that scanned successfully with at least one named tool.
      - errored_cache_keys: keys whose scan errored (their previous cache
        entry should be preserved, not deleted).

    Servers with no cache key or no tools are skipped. A successful scan wins
    over an errored one. Conflicting successful tool hashes are retained as an
    ambiguity list.
    """
    observations: Dict[str, Dict[str, Set[str]]] = {}
    successful_keys: Set[str] = set()
    errored: Set[str] = set()
    for project in projects or []:
        if not isinstance(project, dict):
            continue
        for server in project.get("mcpServers") or []:
            if not isinstance(server, dict):
                continue
            cache_key = cache_key_for_server(server)
            if not cache_key:
                continue
            scan = server.get("scan")
            scan_failed = not isinstance(scan, dict) or scan.get("error") is not None
            if scan_failed:
                errored.add(cache_key)
                continue
            hashes = tool_hashes_from_scan(scan)
            if hashes:
                successful_keys.add(cache_key)
                by_tool = observations.setdefault(cache_key, {})
                for tool_name, content_hash in hashes.items():
                    by_tool.setdefault(tool_name, set()).add(content_hash)
    entries = {
        cache_key: {
            tool_name: next(iter(content_hashes))
            if len(content_hashes) == 1 else sorted(content_hashes)
            for tool_name, content_hashes in by_tool.items()
        }
        for cache_key, by_tool in observations.items()
    }
    # A key that both errored (one project) and succeeded (another) keeps the
    # successful entry.
    errored.difference_update(successful_keys)
    return entries, errored


def read_mcp_tools_cache() -> Dict:
    path = _cache_path()
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"mcp-tools-cache read failed, treating as empty: {e}")
        report_to_sentry(e, {"phase": "mcp_tools_cache"}, level="warning")
        return {}


def _atomic_write(data: Dict) -> None:
    """mkstemp + os.replace, same pattern (and fallback-dir symlink guard) as
    cache.atomic_write_cache."""
    try:
        if _state.UNBOUND_DIR != _state._HOME_STATE_DIR and _state.UNBOUND_DIR.is_symlink():
            logger.warning(f"mcp-tools-cache write skipped: fallback state dir is a symlink: {_state.UNBOUND_DIR}")
            return
        _state.UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".mcp-tools-cache.", suffix=".tmp", dir=str(_state.UNBOUND_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, _cache_path())
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError as e:
        logger.warning(f"mcp-tools-cache write failed: {e}")
        report_to_sentry(e, {"phase": "mcp_tools_cache"}, level="warning")


def _get_subtree(parent: Dict, key: str) -> Dict:
    """parent[key] as a dict, resetting non-dict junk (same defensive shape
    handling as cache.update_tool)."""
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def update_user_entries(coding_tool: str, home_user: str,
                        server_entries: Dict[str, Dict],
                        errored_cache_keys: Optional[Set[str]] = None) -> None:
    """Replace the (coding_tool, home_user) subtree with `server_entries`.

    Cache keys in `errored_cache_keys` keep their previous entry (a scan error
    must not evict a still-configured server from the hot-path cache).
    Empty subtrees are pruned so the file doesn't accumulate dead keys.
    """
    data = read_mcp_tools_cache()
    tools = _get_subtree(data, "tools")
    by_user = _get_subtree(tools, coding_tool)
    previous = by_user.get(home_user)
    previous = previous if isinstance(previous, dict) else {}

    fresh = dict(server_entries)
    for cache_key in (errored_cache_keys or ()):
        if cache_key not in fresh and isinstance(previous.get(cache_key), dict):
            fresh[cache_key] = previous[cache_key]

    if fresh:
        by_user[home_user] = fresh
    else:
        by_user.pop(home_user, None)
        if not by_user:
            tools.pop(coding_tool, None)

    data["updated_at"] = _state._now_iso()
    _atomic_write(data)


def upsert_server_entry(coding_tool: str, home_user: str, cache_key: str,
                        tool_hashes: Dict[str, str]) -> None:
    """Merge ONE server's entry into the (coding_tool, home_user) subtree
    (single-server scan path — sibling servers are unknown there and must be
    left untouched)."""
    if not cache_key or not tool_hashes:
        return
    if not _state._ensure_state_dir():
        return
    # Only augment an existing cache — never create one from a single-server
    # scan. The full discovery run owns the file's existence; a reactive scan
    # on a device that never ran discovery leaves no stray cache behind.
    if not _cache_path().exists():
        return
    data = read_mcp_tools_cache()
    tools = _get_subtree(data, "tools")
    by_user = _get_subtree(tools, coding_tool)
    user_entries = _get_subtree(by_user, home_user)
    current = user_entries.get(cache_key)
    current = current if isinstance(current, dict) else {}
    merged = dict(current)
    for tool_name, content_hash in tool_hashes.items():
        observed = merged.get(tool_name)
        if isinstance(observed, str):
            merged[tool_name] = (
                observed if observed == content_hash
                else sorted({observed, content_hash})
            )
        elif isinstance(observed, list):
            merged[tool_name] = sorted({
                value for value in [*observed, content_hash]
                if isinstance(value, str)
            })
        else:
            merged[tool_name] = content_hash
    user_entries[cache_key] = merged
    data["updated_at"] = _state._now_iso()
    _atomic_write(data)
