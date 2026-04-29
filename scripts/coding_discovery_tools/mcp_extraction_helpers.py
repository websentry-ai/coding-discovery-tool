"""
Shared helper functions for MCP config extraction across all platforms.

These functions are used by both Cursor and Claude Code MCP extractors
on Windows and macOS to avoid code duplication.
"""

import datetime
import json
import logging
import os
import platform
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, List, Dict, Optional, Callable, Tuple, Union

from .constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP tool-list scanning
# ---------------------------------------------------------------------------
# Every call to transform_mcp_servers_to_array also connects to each MCP server
# and captures its tool list (name / title / description per tool). The scan
# result is attached as a `scan` field on each server entry — either:
#   { "tools": [...], "tool_count": N, "server_info": {...}, "error": null }
# or:
#   { "tools": null, "error": { "code", "details" } }

_SCAN_MAX_WORKERS = 4

# Module-level caches: OAuth tokens are fetched at most once per process,
# and scan results are memoized by (url, command, args) so multiple extractors
# finding the same server (e.g. Claude global + Cursor global) scan it once.
_OAUTH_INDEX_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_OAUTH_INDEX_BUILT = False
_SCAN_CACHE: Dict[Tuple[str, str, Tuple[str, ...]], Dict[str, Any]] = {}


def _read_claude_oauth_blob() -> Optional[Dict[str, Any]]:
    """Fetch the Claude Code credentials JSON blob.
      macOS: keychain service "Claude Code-credentials" (prompts on first access).
      Linux/Windows: ~/.claude/.credentials.json (plaintext, mode 0600)."""
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", "Claude Code-credentials",
                    "-a", os.environ.get("USER", ""),
                    "-w",
                ],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:
            logger.debug("keychain read failed: %s", exc)
            return None
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return None

    candidates = [Path.home() / ".claude" / ".credentials.json"]
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg_dir:
        candidates.insert(0, Path(cfg_dir) / ".credentials.json")
    for p in candidates:
        try:
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


def _get_claude_oauth_index() -> Dict[str, Dict[str, Any]]:
    """Build { serverUrl -> {access_token, expires_at_ms, ...} }, memoized."""
    global _OAUTH_INDEX_CACHE, _OAUTH_INDEX_BUILT
    if _OAUTH_INDEX_BUILT:
        return _OAUTH_INDEX_CACHE or {}
    _OAUTH_INDEX_BUILT = True
    blob = _read_claude_oauth_blob() or {}
    mcp = blob.get("mcpOAuth") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for entry in mcp.values():
        url = entry.get("serverUrl")
        if not isinstance(url, str):
            continue
        compact = {
            "access_token": entry.get("accessToken"),
            "refresh_token": entry.get("refreshToken"),
            "expires_at_ms": int(entry.get("expiresAt") or 0),
            "client_id": entry.get("clientId"),
            "scope": entry.get("scope"),
        }
        existing = out.get(url)
        if existing is None or compact["expires_at_ms"] > existing.get("expires_at_ms", 0):
            out[url] = compact
    _OAUTH_INDEX_CACHE = out
    return out


def _maybe_inject_bearer(cfg: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    """If the config's URL matches a fresh Claude-Code-cached token, return a
    copy of cfg with Authorization: Bearer injected. Never overrides an
    existing Authorization header the config may already set."""
    url = cfg.get("url")
    if not url:
        return cfg
    existing_headers = cfg.get("headers") or {}
    if any(isinstance(k, str) and k.lower() == "authorization" for k in existing_headers):
        return cfg
    token = _get_claude_oauth_index().get(url)
    if not token or not token.get("access_token"):
        return cfg
    if token["expires_at_ms"] and token["expires_at_ms"] < now_ms + 60_000:
        return cfg  # expired — user needs to re-auth via Claude Code
    return {
        **cfg,
        "headers": {**existing_headers, "Authorization": f"Bearer {token['access_token']}"},
    }


def _scan_cache_key(cfg: Dict[str, Any]) -> Tuple[str, str, Tuple[str, ...]]:
    return (
        cfg.get("url") or "",
        cfg.get("command") or "",
        tuple(cfg.get("args") or []),
    )


_TOOL_FIELDS = (
    "name",
    "title",
    "description",
    "inputSchema",
    "outputSchema",
    "annotations",
)


def _trim_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Project each tool to the fields the backend cares about. Drops any
    server-supplied keys we don't consume (vendor extensions, internal flags)
    so the payload stays bounded."""
    if not tools:
        return None
    return [
        {k: t.get(k) for k in _TOOL_FIELDS if k in t}
        for t in tools
        if isinstance(t, dict)
    ]


# Scanner-output fields surfaced under error.details
_DETAIL_FIELDS = (
    "http_status",        # HTTP status code observed from the server
    "www_authenticate",   # WWW-Authenticate header verbatim
    "oauth",              # data fetched from the server's .well-known endpoints
    "content_type",       # Content-Type header from the server
    "body_excerpt",       # truncated server response body
    "parsed",             # server response parsed as JSON-RPC but missing required fields
    "stderr_tail",        # tail of subprocess stderr
    "exit_code",          # subprocess exit code
    "package",            # npm package name extracted from registry-404 stderr
    "path",               # filesystem path extracted from ENOENT stderr
    "errors",             # per-RPC observed errors during pagination
)


def _translate_scan_result(scanner_result: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the scanner module's internal {status, ...} dict into the
    public {tools | error} shape that lands in the discovery payload."""
    status = scanner_result.get("status")
    scanned_at = scanner_result.get("scanned_at")

    if status in ("scanned", "scanned_partial", "scanned_empty"):
        return {
            "scanned_at": scanned_at,
            "tools": _trim_tools(scanner_result.get("tools")),
            "tool_count": scanner_result.get("tool_count"),
            "server_info": scanner_result.get("server_info"),
            "error": None,
        }

    details: Dict[str, Any] = {}
    for field in _DETAIL_FIELDS:
        value = scanner_result.get(field)
        if value is not None:
            details[field] = value
    raw_error = scanner_result.get("error")
    if raw_error is not None:
        details["raw_error"] = raw_error

    return {
        "scanned_at": scanned_at,
        "tools": None,
        "tool_count": None,
        "server_info": scanner_result.get("server_info"),
        "error": {
            "code": status or "scanner_error",
            "details": details or None,
        },
    }


def _run_one_scan(cfg_with_auth: Dict[str, Any]) -> Dict[str, Any]:
    from .mcp_tool_scanner import scan_mcp_server
    try:
        result = scan_mcp_server(cfg_with_auth)
    except Exception as exc:
        logger.warning("scan_mcp_server raised: %s", exc, exc_info=True)
        return {
            "scanned_at": None,
            "tools": None,
            "tool_count": None,
            "server_info": None,
            "error": {
                "code": "scanner_error",
                "details": {"raw_error": f"{type(exc).__name__}: {exc}"},
            },
        }
    return _translate_scan_result(result)


def _check_expired_token(cfg: Dict[str, Any], now_ms: int) -> Optional[Dict[str, Any]]:
    """Return an auth_expired scan result if this config's URL has a Claude
    Code OAuth token in the keychain but the token is past its expiry. Else None.

    Short-circuits before the scan so we don't fall through to a misleading
    auth_required from the server itself."""
    url = cfg.get("url")
    if not url:
        return None
    existing_headers = cfg.get("headers") or {}
    if any(isinstance(k, str) and k.lower() == "authorization" for k in existing_headers):
        return None  # caller already provides explicit auth
    token = _get_claude_oauth_index().get(url)
    if not token or not token.get("access_token"):
        return None
    expires_at_ms = token.get("expires_at_ms") or 0
    if not expires_at_ms or expires_at_ms >= now_ms + 60_000:
        return None  # token still good (or no expiry recorded)

    expired_at_iso = (
        datetime.datetime.fromtimestamp(expires_at_ms / 1000.0, tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )
    scanned_at = (
        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    )
    return {
        "scanned_at": scanned_at,
        "tools": None,
        "tool_count": None,
        "server_info": None,
        "error": {
            "code": "auth_expired",
            "details": {"expired_at": expired_at_iso},
        },
    }


def _scan_servers_in_mapping(
    mcp_servers: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Scan every server in the {name: cfg} mapping in parallel. Returns a
    parallel {name: scan_result} mapping. Unmodified when scanning is off."""
    now_ms = int(time.time() * 1000)
    results: Dict[str, Dict[str, Any]] = {}
    pending: List[Tuple[str, Dict[str, Any], Tuple[str, str, Tuple[str, ...]]]] = []

    for name, cfg in mcp_servers.items():
        if not isinstance(cfg, dict):
            continue
        key = _scan_cache_key(cfg)
        if key in _SCAN_CACHE:
            results[name] = _SCAN_CACHE[key]
            continue
        # Short-circuit known-expired Claude OAuth tokens — skip the scan and
        # report auth_expired up front instead of waiting for a 401 from the
        # server (which would be reported, less helpfully, as auth_required).
        expired = _check_expired_token(cfg, now_ms)
        if expired is not None:
            _SCAN_CACHE[key] = expired
            results[name] = expired
            continue
        pending.append((name, _maybe_inject_bearer(cfg, now_ms), key))

    if not pending:
        return results

    worker_count = min(_SCAN_MAX_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(_run_one_scan, cfg): (name, key) for name, cfg, key in pending}
        for fut in as_completed(futures):
            name, key = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {
                    "scanned_at": None,
                    "tools": None,
                    "tool_count": None,
                    "server_info": None,
                    "error": {
                        "code": "scanner_error",
                        "details": {"raw_error": f"{type(exc).__name__}: {exc}"},
                    },
                }
            _SCAN_CACHE[key] = res
            results[name] = res
    return results

# Claude Code (project-level)
MCP_CLAUDE_PROJECT_FILENAMES = [".mcp.json"]

# Windsurf (global: ~/.codeium/windsurf/mcp_config.json)
MCP_CONFIG_JSON_FILENAMES = ["mcp_config.json"]

# VS Code, Cursor, others
MCP_JSON_FILENAMES = ["mcp.json"]


def transform_mcp_servers_to_array(mcp_servers: Dict) -> List[Dict]:
    """
    Transform mcpServers from object format to array format.

    Excludes 'env' and 'headers' fields from server configs as they're not
    needed in the output. Each server is also scanned for its live tool list
    (parallel, memoized across calls) and a `scan` field is attached:

      - on success: { "scanned_at", "tools": [...], "tool_count", "server_info", "error": null }
      - on failure: { "scanned_at", "tools": null, "tool_count": null, "server_info",
                      "error": { "code", "details" } }

    Tool entries are trimmed to {name, title, description}. The scanner
    re-uses Claude Code's keychain OAuth tokens (macOS) or
    ~/.claude/.credentials.json (Linux/Windows) for URLs that match.

    Args:
        mcp_servers: Dictionary mapping server names to server configs

    Returns:
        List of server config objects with 'name' field added (env and
        headers excluded; 'scan' field always present when the input is a
        dict).
    """
    if not isinstance(mcp_servers, dict):
        return []

    # Fields to exclude from server configs
    excluded_fields = {"env", "headers"}

    # Scan each server for its tool list before we strip credentials.
    scan_results: Dict[str, Dict[str, Any]] = {}
    try:
        scan_results = _scan_servers_in_mapping(mcp_servers)
    except Exception as exc:
        logger.warning("MCP scan pass failed: %s", exc, exc_info=True)
        scan_results = {}

    servers_array = []
    for server_name, server_config in mcp_servers.items():
        if isinstance(server_config, dict):
            # Create server object excluding 'env' and 'headers' fields
            server_obj = {
                "name": server_name,
                **{field_name: field_value for field_name, field_value in server_config.items()
                    if field_name not in excluded_fields}
            }
            scan = scan_results.get(server_name)
            if scan:
                server_obj["scan"] = scan
            servers_array.append(server_obj)

    return servers_array


def extract_mcp_from_dir_generic(
    tool_dir: Path,
    projects: List[Dict],
    config_filename: Union[str, List[str]],
    tool_name: str,
    global_tool_dir: Optional[Path] = None
) -> None:
    """
    Generic function to extract MCP config from a tool directory.

    This replaces all tool-specific extract_*_mcp_from_dir functions.

    Args:
        tool_dir: Path to the tool directory (e.g., .cursor, .windsurf, .roo, .kilocode)
        projects: List to append project configs to
        config_filename: Name(s) of the MCP config file (e.g., "mcp.json" or ["mcp.json", "mcp.JSON", "MCP.json"])
        tool_name: Name of the tool (for logging)
        global_tool_dir: Path to global tool directory to skip (optional)
    """
    # Normalize config_filename to a list
    config_filenames = [config_filename] if isinstance(config_filename, str) else config_filename

    # Try each possible filename variation
    mcp_config_file = None
    for filename in config_filenames:
        candidate = tool_dir / filename
        if candidate.exists():
            mcp_config_file = candidate
            break

    if mcp_config_file is None:
        return
    
    try:
        project_root = tool_dir.parent
        
        # Skip if this is the global config directory
        if global_tool_dir and tool_dir == global_tool_dir:
            return
        
        content = mcp_config_file.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only add if there are MCP servers configured
        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in {tool_name} MCP config {mcp_config_file}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading {tool_name} MCP config {mcp_config_file}: {e}")
    except Exception as e:
        logger.warning(f"Error reading {tool_name} MCP config {mcp_config_file}: {e}")


def walk_for_mcp_configs_generic(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    tool_dir_name: str,
    config_filename: Union[str, List[str]],
    tool_name: str,
    global_tool_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Generic function to recursively walk directory tree looking for tool MCP config files.

    This replaces all tool-specific walk_for_*_mcp_configs functions.

    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        tool_dir_name: Name of the tool directory to look for (e.g., ".cursor", ".windsurf")
        config_filename: Name(s) of the MCP config file (e.g., "mcp.json" or ["mcp.json", "mcp.JSON"])
        tool_name: Name of the tool (for logging)
        global_tool_dir: Path to global tool directory to skip (optional)
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return
    
    try:
        for item in current_dir.iterdir():
            try:
                # Check if we should skip this path
                if should_skip_func(item):
                    continue
                
                # Check depth
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    # Path not relative to root (different drive on Windows)
                    continue
                
                if item.is_dir():
                    # Found the tool directory!
                    if item.name.lower() == tool_dir_name.lower():
                        extract_mcp_from_dir_generic(
                            item, projects, config_filename, tool_name, global_tool_dir
                        )
                        # Don't recurse into tool directory
                        continue
                    
                    if item.is_symlink():
                        continue

                    # Recurse into subdirectories
                    walk_for_mcp_configs_generic(
                        root_path, item, projects, tool_dir_name, config_filename,
                        tool_name, global_tool_dir, should_skip_func, current_depth + 1
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


def extract_claude_mcp_fields(config_data: Dict, config_path: Path) -> List[Dict]:
    """
    Extract MCP-related fields from Claude Code configuration.
    
    Args:
        config_data: Full configuration dictionary
        
    Returns:
        List of project dicts with MCP configuration
    """
    projects = []

    # Extract user-level (global) mcpServers from root of config
    if "mcpServers" in config_data and isinstance(config_data["mcpServers"], dict):
        user_mcp_servers_obj = config_data["mcpServers"]
        user_mcp_servers_array = transform_mcp_servers_to_array(user_mcp_servers_obj)

        if user_mcp_servers_array:
            projects.append({
                "path": str(config_path),
                "mcpServers": user_mcp_servers_array,
                "scope": "user"
            })

    # Extract project-level mcpServers from projects
    if "projects" in config_data and isinstance(config_data["projects"], dict):
        for project_path, project_data in config_data["projects"].items():
            if not isinstance(project_data, dict):
                continue
            
            # Transform mcpServers from object to array
            mcp_servers_obj = project_data.get("mcpServers", {})
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
                
            project_mcp = {
                "path": project_path,
                "mcpServers": mcp_servers_array,
                "mcpContextUris": project_data.get("mcpContextUris", []),
                "enabledMcpjsonServers": project_data.get("enabledMcpjsonServers", []),
                "disabledMcpjsonServers": project_data.get("disabledMcpjsonServers", []),
                "scope": "project"
            }
            
            projects.append(project_mcp)
    
    return projects


def extract_cursor_mcp_from_dir(
    cursor_dir: Path,
    projects: List[Dict],
    global_cursor_dir: Path
) -> None:
    """
    Extract MCP config from a .cursor directory if mcp.json exists.
    
    Args:
        cursor_dir: Path to .cursor directory
        projects: List to append project configs to
        global_cursor_dir: Path to global .cursor directory to skip
    """
    extract_mcp_from_dir_generic(
        cursor_dir, projects, MCP_JSON_FILENAMES, "Cursor", global_cursor_dir
    )


def walk_for_cursor_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_cursor_dir: Path,
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .cursor/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_cursor_dir: Path to global .cursor directory to skip
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".cursor", MCP_JSON_FILENAMES,
        "Cursor", global_cursor_dir, should_skip_func, current_depth
    )


def extract_windsurf_mcp_from_dir(
    windsurf_dir: Path,
    projects: List[Dict],
    global_windsurf_dir: Path
) -> None:
    """
    Extract MCP config from a .windsurf directory if mcp_config.json exists.
    
    Args:
        windsurf_dir: Path to .windsurf directory
        projects: List to append project configs to
        global_windsurf_dir: Path to global .windsurf directory to skip
    """
    extract_mcp_from_dir_generic(
        windsurf_dir, projects, MCP_CONFIG_JSON_FILENAMES, "Windsurf", global_windsurf_dir
    )


def walk_for_windsurf_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_windsurf_dir: Path,
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .windsurf/mcp_config.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_windsurf_dir: Path to global .windsurf directory to skip
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".windsurf", MCP_CONFIG_JSON_FILENAMES,
        "Windsurf", global_windsurf_dir, should_skip_func, current_depth
    )


def extract_roo_mcp_from_dir(
    roo_dir: Path,
    projects: List[Dict],
    global_roo_dir: Optional[Path] = None
) -> None:
    """
    Extract MCP config from a .roo directory if mcp.json exists.
    
    Args:
        roo_dir: Path to .roo directory
        projects: List to append project configs to
        global_roo_dir: Path to global .roo directory to skip (optional)
    """
    extract_mcp_from_dir_generic(
        roo_dir, projects, MCP_JSON_FILENAMES, "Roo Code", global_roo_dir
    )


def walk_for_roo_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_roo_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .roo/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_roo_dir: Path to global .roo directory to skip (optional)
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".roo", MCP_JSON_FILENAMES,
        "Roo Code", global_roo_dir, should_skip_func, current_depth
    )


def extract_kilocode_mcp_from_dir(
    kilocode_dir: Path,
    projects: List[Dict],
    global_kilocode_dir: Optional[Path] = None
) -> None:
    """
    Extract MCP config from a .kilocode directory if mcp.json exists.
    
    Args:
        kilocode_dir: Path to .kilocode directory
        projects: List to append project configs to
        global_kilocode_dir: Path to global .kilocode directory to skip (optional)
    """
    extract_mcp_from_dir_generic(
        kilocode_dir, projects, MCP_JSON_FILENAMES, "Kilo Code", global_kilocode_dir
    )


def walk_for_kilocode_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_kilocode_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .kilocode/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_kilocode_dir: Path to global .kilocode directory to skip (optional)
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".kilocode", MCP_JSON_FILENAMES,
        "Kilo Code", global_kilocode_dir, should_skip_func, current_depth
    )


def read_global_mcp_config(
    config_path: Path,
    tool_name: str = "MCP",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Read and parse a global MCP config file.
    
    This is a shared helper for reading global MCP configs that follow the standard pattern:
    - Read JSON file
    - Extract mcpServers object
    - Transform to array
    - Return dict with path and mcpServers
    
    Args:
        config_path: Path to the MCP config JSON file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path (default: 2)
                      For ~/.cursor/mcp.json -> 2 levels up = ~
                      For ~/.gemini/antigravity/mcp_config.json -> 3 levels up = ~
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only return if there are MCP servers configured
        if mcp_servers_array:
            # Calculate the global config path by going up parent_levels
            global_config_path = config_path
            for _ in range(parent_levels):
                global_config_path = global_config_path.parent
            return {
                "path": str(global_config_path),
                "mcpServers": mcp_servers_array
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in global {tool_name} MCP config {config_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = "MCP",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Extract global MCP config with support for root/admin user (checks all users).
    
    When running as root/admin, this function checks all user directories
    and returns the first non-empty config found.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    import platform
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, prioritize checking user directories first
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                # Build user-specific config path
                # global_config_path is like ~/.cursor/mcp.json
                # We need to replace ~ with user_dir
                try:
                    user_config_path = user_dir / global_config_path.relative_to(Path.home())
                    if user_config_path.exists():
                        config = read_global_mcp_config(user_config_path, tool_name, parent_levels)
                        if config:
                            return config
                except (ValueError, OSError):
                    # Path might not be relative to home, try direct construction
                    continue
        
        # Fallback to admin's own global config if no user config found
        if global_config_path.exists():
            return read_global_mcp_config(global_config_path, tool_name, parent_levels)
    else:
        # For regular users, check their own home directory
        if global_config_path.exists():
            return read_global_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


def extract_ide_global_configs_with_root_support(
    extract_configs_for_user_func,
    tool_name: str = "MCP"
) -> List[Dict]:
    """
    Extract global MCP configs from IDE global storage with support for root user.
    
    This helper is for tools like Cline and Roo Code that store configs in IDE global storage
    (multiple configs per user, one per IDE).
    
    When running as root/admin, this function checks all user directories
    and returns all configs found.
    
    Args:
        extract_configs_for_user_func: Function that extracts configs for a specific user
                                      Signature: func(user_home: Path) -> List[Dict]
        tool_name: Name of the tool (for logging)
    
    Returns:
        List of config dicts with 'path' and 'mcpServers' keys
    """
    import platform
    
    all_configs = []
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, check all users
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    user_configs = extract_configs_for_user_func(user_dir)
                    all_configs.extend(user_configs)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir} for {tool_name}: {e}")
                    continue
        
        # Also check root/admin's own configs
        try:
            root_configs = extract_configs_for_user_func(Path.home())
            all_configs.extend(root_configs)
        except Exception as e:
            logger.debug(f"Error extracting root configs for {tool_name}: {e}")
    else:
        # For regular users, check their own home directory
        try:
            user_configs = extract_configs_for_user_func(Path.home())
            all_configs.extend(user_configs)
        except Exception as e:
            logger.debug(f"Error extracting user configs for {tool_name}: {e}")
    
    return all_configs


def read_ide_global_mcp_config(
    config_path: Path,
    tool_name: str = "MCP",
    use_full_path: bool = True
) -> Optional[Dict]:
    """
    Read and parse a global MCP config file from IDE global storage.
    
    Args:
        config_path: Path to the MCP config JSON file
        tool_name: Name of the tool (for logging)
        use_full_path: If True, use the full config_path as the path in result.
                      If False, use parent directory
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only return if there are MCP servers configured
        if mcp_servers_array:
            return {
                "path": str(config_path) if use_full_path else str(config_path.parent),
                "mcpServers": mcp_servers_array
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in global {tool_name} MCP config {config_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_project_level_mcp_configs_with_fallback_windows(
    root_path: Path,
    tool_dir_name: str,
    global_tool_dir: Optional[Path],
    extract_from_dir_func,
    walk_for_configs_func: Callable,
    should_skip_func: Callable[[Path], bool]
) -> List[Dict]:
    """
    Windows-specific helper for extracting project-level MCP configs with root path handling.
    
    This function handles the common pattern for Windows:
    1. If searching from root drive (C:\), get top-level directories and walk each
    2. If searching from non-root, use rglob to find tool directories
    3. Fallback to home directory if root access fails
    
    Uses Windows-specific system directory skipping.
    
    Args:
        root_path: Root directory to search from (root drive for MDM)
        tool_dir_name: Name of the tool directory to search for (e.g., ".cursor", ".windsurf", ".roo")
        global_tool_dir: Path to the global tool directory to skip
        extract_from_dir_func: Function to extract MCP from a found tool directory
                              Signature: func(tool_dir: Path, projects: List, global_dir: Path)
        walk_for_configs_func: Function to recursively walk for MCP configs
                             Signature: func(root_path: Path, current_dir: Path, projects: List,
                                            global_dir: Path, should_skip: Callable, depth: int)
        should_skip_func: Function to check if a path should be skipped (Windows-specific)
    
    Returns:
        List of project dicts with MCP configs
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    projects = []
    
    try:
        # Get top-level directories, skipping system ones using Windows-specific logic
        top_level_dirs = [
            item for item in root_path.iterdir()
            if item.is_dir() and not should_skip_func(item)
        ]
        
        # Use parallel processing for top-level directories
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    walk_for_configs_func,
                    root_path, dir_path, projects, global_tool_dir,
                    should_skip_func, current_depth=1
                )
                for dir_path in top_level_dirs
            }
            
            for future in as_completed(futures):
                try:
                    future.result()  # Raises exception if any occurred
                except Exception as e:
                    logger.debug(f"Error in parallel processing: {e}")
    except (PermissionError, OSError) as e:
        logger.warning(f"Error accessing root directory: {e}")
        # Fallback to home directory
        logger.info("Falling back to home directory search")
        home_path = Path.home()
        
        for tool_dir in home_path.rglob(tool_dir_name):
            try:
                if should_skip_func(tool_dir):
                    continue
                extract_from_dir_func(tool_dir, projects, global_tool_dir)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {tool_dir}: {e}")
                continue
    
    return projects


def extract_claude_project_mcp_from_file(
    mcp_json_path: Path,
    projects: List[Dict]
) -> None:
    """
    Extract MCP config from a project-scope .mcp.json file.
    """
    if not mcp_json_path.exists() or not mcp_json_path.is_file():
        return

    try:
        project_root = mcp_json_path.parent

        content = mcp_json_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)

        mcp_servers_obj = config_data.get("mcpServers", {})

        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array,
                "scope": "project"
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Claude Code project MCP config {mcp_json_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading Claude Code project MCP config {mcp_json_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading Claude Code project MCP config {mcp_json_path}: {e}")


def walk_for_claude_project_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Walk directory tree looking for Claude Code project-scope .mcp.json files.
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return

    try:
        for entry in current_dir.iterdir():
            try:
                if entry.is_dir():
                    if should_skip_func(entry):
                        continue
                    if entry.is_symlink():
                        continue
                    walk_for_claude_project_mcp_configs(
                        root_path, entry, projects,
                        should_skip_func, current_depth + 1
                    )
                elif entry.is_file() and entry.name in MCP_CLAUDE_PROJECT_FILENAMES:
                    try:
                        depth = len(entry.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if should_skip_func(entry):
                        continue

                    extract_claude_project_mcp_from_file(entry, projects)

            except (PermissionError, OSError):
                continue
            except Exception as e:
                logger.debug(f"Error processing {entry}: {e}")
                continue

    except (PermissionError, OSError):
        pass
    except Exception as e:
        logger.debug(f"Error walking {current_dir}: {e}")


def extract_dual_path_configs_with_root_support(
    preferred_path: Path,
    fallback_path: Path,
    extract_from_file_func,
    tool_name: str = "MCP"
) -> List[Dict]:
    """
    Extract configs from dual paths (preferred + fallback) with root user support.
    
    This helper is for tools like Claude Code that have two possible config locations.
    It tries the preferred path first, then falls back to the fallback path.
    
    When running as root/admin, checks all user directories.
    
    Args:
        preferred_path: Preferred config file path (relative to home)
        fallback_path: Fallback config file path (relative to home)
        extract_from_file_func: Function to extract configs from a file
                               Signature: func(config_path: Path) -> List[Dict]
        tool_name: Name of the tool (for logging)
    
    Returns:
        List of config dicts
    """
    import platform
    
    all_projects = []
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, check all users
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                # Try preferred location for this user
                try:
                    user_preferred = user_dir / preferred_path.relative_to(Path.home())
                    if user_preferred.exists():
                        user_projects = extract_from_file_func(user_preferred)
                        if user_projects:
                            all_projects.extend(user_projects)
                            continue
                except (ValueError, OSError):
                    pass
                
                # Try fallback location for this user
                try:
                    user_fallback = user_dir / fallback_path.relative_to(Path.home())
                    if user_fallback.exists():
                        user_projects = extract_from_file_func(user_fallback)
                        if user_projects:
                            all_projects.extend(user_projects)
                except (ValueError, OSError):
                    pass
        
        # Also check root/admin's configs
        if preferred_path.exists():
            root_projects = extract_from_file_func(preferred_path)
            if root_projects:
                all_projects.extend(root_projects)
        elif fallback_path.exists():
            root_projects = extract_from_file_func(fallback_path)
            if root_projects:
                all_projects.extend(root_projects)
    else:
        # For regular users, check their own home directory
        if preferred_path.exists():
            user_projects = extract_from_file_func(preferred_path)
            if user_projects:
                all_projects.extend(user_projects)
        elif fallback_path.exists():
            user_projects = extract_from_file_func(fallback_path)
            if user_projects:
                all_projects.extend(user_projects)
    
    return all_projects


def get_managed_mcp_path() -> Optional[Path]:
    """
    Determine the path to the managed-mcp.json file.
    """
    import platform

    system = platform.system()
    if system == "Darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-mcp.json")
    elif system == "Windows":
        return Path("C:/Program Files/ClaudeCode/managed-mcp.json")
    return None


def extract_managed_mcp_config(projects: List[Dict]) -> None:
    """
    Extract MCP config from the managed-mcp.json file.
    """
    managed_path = get_managed_mcp_path()
    if not managed_path or not managed_path.exists() or not managed_path.is_file():
        return

    try:
        content = managed_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)

        mcp_servers_obj = config_data.get("mcpServers", {})
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

        if mcp_servers_array:
            projects.append({
                "path": str(managed_path),
                "mcpServers": mcp_servers_array,
                "scope": "managed"
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in managed MCP config {managed_path}: {e}")
    except PermissionError as e:
        logger.debug(f"Permission denied reading managed MCP config {managed_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading managed MCP config {managed_path}: {e}")


def extract_claudeai_mcp_servers(claude_dir: Path, projects: List[Dict]) -> None:
    """
    Extract cloud-synced MCP server names from claude.ai auth cache.

    Reads ~/.claude/mcp-needs-auth-cache.json and extracts server names
    prefixed with "claude.ai ". These are cloud-synced MCP servers whose
    full config lives server-side; only names are available locally.

    Args:
        claude_dir: Path to the .claude directory (e.g., ~/.claude)
        projects: List to append results to
    """
    cache_file = claude_dir / "mcp-needs-auth-cache.json"
    if not cache_file.exists() or not cache_file.is_file():
        return

    try:
        content = cache_file.read_text(encoding='utf-8', errors='replace')
        cache_data = json.loads(content)

        if not isinstance(cache_data, dict):
            return

        servers = [
            {"name": key, "scope": "claudeai"}
            for key in cache_data
            if key.startswith("claude.ai ")
        ]

        if servers:
            projects.append({
                "path": str(claude_dir),
                "mcpServers": servers,
                "scope": "claudeai"
            })
    except json.JSONDecodeError as e:
        logger.debug(f"Invalid JSON in claude.ai auth cache {cache_file}: {e}")
    except PermissionError as e:
        logger.debug(f"Permission denied reading claude.ai auth cache {cache_file}: {e}")
    except Exception as e:
        logger.debug(f"Error reading claude.ai auth cache {cache_file}: {e}")


def extract_claudeai_mcp_servers_with_root_support(projects: List[Dict]) -> None:
    """
    Extract cloud-synced MCP server names with root user support.

    When running as root/admin, scans all user directories for
    ~/.claude/mcp-needs-auth-cache.json. Otherwise checks only
    the current user's home directory.
    """
    import platform

    is_admin = False
    users_dir = None

    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass

    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                claude_dir = user_dir / ".claude"
                if claude_dir.exists() and claude_dir.is_dir():
                    try:
                        extract_claudeai_mcp_servers(claude_dir, projects)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Error scanning claude.ai servers for user {user_dir.name}: {e}")

        extract_claudeai_mcp_servers(Path.home() / ".claude", projects)
    else:
        extract_claudeai_mcp_servers(Path.home() / ".claude", projects)


def extract_plugin_mcp_from_plugin_json(
    plugin_json_path: Path,
    projects: List[Dict]
) -> None:
    """
    Extract MCP config from a plugin's plugin.json file.
    """
    if not plugin_json_path.exists() or not plugin_json_path.is_file():
        return

    try:
        plugin_root = plugin_json_path.parent
        content = plugin_json_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)

        mcp_servers_obj = config_data.get("mcpServers", {})
        if not mcp_servers_obj:
            return

        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

        if mcp_servers_array:
            plugin_name = config_data.get("name", plugin_root.name)
            projects.append({
                "path": str(plugin_root),
                "mcpServers": mcp_servers_array,
                "scope": "plugin",
                "pluginName": plugin_name
            })
    except json.JSONDecodeError as e:
        logger.debug(f"Invalid JSON in plugin.json {plugin_json_path}: {e}")
    except PermissionError as e:
        logger.debug(f"Permission denied reading plugin.json {plugin_json_path}: {e}")
    except Exception as e:
        logger.debug(f"Error reading plugin.json {plugin_json_path}: {e}")


def _scan_plugin_cache_dir(cache_dir: Path, projects: List[Dict]) -> None:
    """
    Scan the plugin cache directory for MCP configs.

    Handles the nested structure:
        cache/<marketplace>/<plugin>/<version>/.mcp.json
        cache/<marketplace>/<plugin>/<version>/.claude-plugin/plugin.json

    Args:
        cache_dir: Path to ~/.claude/plugins/cache
        projects: List to append results to
    """
    if not cache_dir.exists() or not cache_dir.is_dir():
        return

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

                            mcp_file = version_dir / ".mcp.json"
                            if mcp_file.exists() and mcp_file.is_file():
                                _extract_plugin_mcp_from_dot_mcp_json(
                                    mcp_file, plugin_dir.name, projects
                                )

                            claude_plugin_json = version_dir / ".claude-plugin" / "plugin.json"
                            if claude_plugin_json.exists():
                                extract_plugin_mcp_from_plugin_json(claude_plugin_json, projects)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError) as e:
        logger.debug(f"Error scanning plugin cache directory {cache_dir}: {e}")
    except Exception as e:
        logger.debug(f"Error extracting plugin cache MCP configs: {e}")


def _extract_plugin_mcp_from_dot_mcp_json(
    mcp_json_path: Path,
    plugin_name: str,
    projects: List[Dict]
) -> None:
    """
    Extract MCP config from a plugin's .mcp.json file in the cache directory.

    Args:
        mcp_json_path: Path to the .mcp.json file
        plugin_name: Name of the plugin (directory name)
        projects: List to append results to
    """
    try:
        content = mcp_json_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)

        mcp_servers_obj = config_data.get("mcpServers")
        if not mcp_servers_obj:
            mcp_servers_obj = {k: v for k, v in config_data.items() if isinstance(v, dict)}
        if not mcp_servers_obj:
            return

        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

        if mcp_servers_array:
            projects.append({
                "path": str(mcp_json_path.parent),
                "mcpServers": mcp_servers_array,
                "scope": "plugin",
                "pluginName": plugin_name
            })
    except json.JSONDecodeError as e:
        logger.debug(f"Invalid JSON in plugin .mcp.json {mcp_json_path}: {e}")
    except PermissionError as e:
        logger.debug(f"Permission denied reading plugin .mcp.json {mcp_json_path}: {e}")
    except Exception as e:
        logger.debug(f"Error reading plugin .mcp.json {mcp_json_path}: {e}")


def extract_claude_plugin_mcp_configs(projects: List[Dict]) -> None:
    """
    Extract MCP configs from Claude Code plugins.
    """
    plugins_dir = Path.home() / ".claude" / "plugins"
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return

    try:
        for plugin_dir in plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            plugin_json = plugin_dir / "plugin.json"
            if plugin_json.exists():
                extract_plugin_mcp_from_plugin_json(plugin_json, projects)
    except (PermissionError, OSError) as e:
        logger.debug(f"Error scanning plugins directory {plugins_dir}: {e}")
    except Exception as e:
        logger.debug(f"Error extracting plugin MCP configs: {e}")

    _scan_plugin_cache_dir(plugins_dir / "cache", projects)


def extract_claude_plugin_mcp_configs_with_root_support(projects: List[Dict]) -> None:
    """
    Extract MCP configs from Claude Code plugins with root user support.
    """
    import platform

    is_admin = False
    users_dir = None

    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass

    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                plugins_dir = user_dir / ".claude" / "plugins"
                if plugins_dir.exists() and plugins_dir.is_dir():
                    try:
                        for plugin_dir in plugins_dir.iterdir():
                            if not plugin_dir.is_dir():
                                continue
                            plugin_json = plugin_dir / "plugin.json"
                            if plugin_json.exists():
                                extract_plugin_mcp_from_plugin_json(plugin_json, projects)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Error scanning plugins for user {user_dir.name}: {e}")

                    _scan_plugin_cache_dir(plugins_dir / "cache", projects)

        extract_claude_plugin_mcp_configs(projects)
    else:
        extract_claude_plugin_mcp_configs(projects)

