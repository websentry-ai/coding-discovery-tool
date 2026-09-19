r"""
MCP config extraction for GitHub Copilot in Visual Studio (Windows).

Visual Studio reads MCP servers from five locations
(learn.microsoft.com/visualstudio/ide/mcp-servers), but four of them are shared
with editors this tool already inventories:

    %USERPROFILE%\.mcp.json          -> split by key: Claude Code takes mcpServers, here takes servers
    <SOLUTIONDIR>\.mcp.json          -> Claude Code / Copilot CLI
    <SOLUTIONDIR>\.vscode\mcp.json   -> GitHub Copilot (VS Code)
    <SOLUTIONDIR>\.cursor\mcp.json   -> Cursor
    <SOLUTIONDIR>\.vs\mcp.json       -> here

Visual Studio borrows the other editors' files on purpose, so claiming them would
duplicate every server already attributed elsewhere rather than discover it.

``.vs\mcp.json`` is Visual Studio's alone. ``%USERPROFILE%\.mcp.json`` is shared
with Claude Code, so ownership splits on the key each tool actually parses —
Claude Code reads ``mcpServers``, Visual Studio writes ``servers``. It is also the
one Visual Studio MCP location under the user's home, so it is the only one that
survives ``filter_tool_projects_by_user`` on the conventional ``C:\src\...``
solution layout. A Visual Studio row still reports fewer servers than the IDE
actually loads: a deliberate under-report, not a miss.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH, SKIP_DIRS, is_symlink_or_junction
from ...mcp_extraction_helpers import (
    read_mcp_json,
    transform_mcp_servers_to_array,
    _strip_jsonc_comments,
    _strip_trailing_commas,
)
from ...windows_extraction_helpers import (
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)

logger = logging.getLogger(__name__)

MCP_FILENAME = "mcp.json"
VS_DIR_NAME = ".vs"

# Sentinel so a cached empty list still short-circuits.
_UNSET = object()
USER_MCP_FILENAME = ".mcp.json"


def _visual_studio_servers(mcp_json: Path) -> List[Dict]:
    """The ``servers`` half of a shared user-scope file, as a server array.

    The two tools read different keys -- Claude Code takes ``mcpServers`` and
    nothing else (mcp_extraction_helpers.py:2486), Visual Studio writes
    ``servers`` -- so a file carrying both is SPLIT, not handed to one of them.
    Dropping it whole would lose every Visual Studio server on a mixed file;
    claiming it whole would double-count every Claude Code one.

    JSONC, not JSON: Visual Studio writes this file through an editor that
    tolerates comments, trailing commas and a BOM, and the shared reader strips
    all three. Parsing it raw rejects a valid config as an empty server list and
    silently drops it from discovery.

    Never raises: an unreadable or malformed file yields nothing, which
    under-reports rather than duplicating.
    """
    try:
        content = mcp_json.read_text(encoding="utf-8-sig", errors="replace")
        data = json.loads(_strip_trailing_commas(_strip_jsonc_comments(content)))
    except (OSError, ValueError) as exc:
        logger.debug(f"Could not parse {mcp_json}: {exc}")
        return []
    if not isinstance(data, dict):
        return []
    return transform_mcp_servers_to_array(data.get("servers") or {})


class WindowsVisualStudioMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Visual Studio MCP config on Windows systems."""

    def __init__(self) -> None:
        # One Copilot row per user, so memoise: the extractor is built once per scan.
        self._solutions_cache = _UNSET
        self._user_scope_cache = _UNSET

    def extract_mcp_config(self, tool_name: Optional[str] = None) -> Optional[Dict]:
        """Solution-scoped ``.vs\\mcp.json`` servers plus ``%USERPROFILE%\\.mcp.json``."""
        projects: List[Dict] = []
        projects.extend(self._solution_configs())
        projects.extend(self._user_scope_configs())

        if not projects:
            return None
        return {"projects": projects}

    def _user_scope_configs(self) -> List[Dict]:
        r"""``%USERPROFILE%\.mcp.json`` — what Visual Studio calls its *global* MCP
        server configuration.

        Only the ``servers`` half is claimed; ``mcpServers`` is left to Claude Code.
        Ownership splits on the key, because the two tools parse different ones:
        Claude Code reads ``mcpServers`` only (mcp_extraction_helpers.py:2486)
        while Visual Studio writes ``servers``. Measured on a live box with both
        installed: a ``servers``-shaped file is claimed by Visual Studio alone, an
        ``mcpServers``-shaped one was claimed by BOTH until ownership split. A file
        carrying both keys is split between the two rather than dropped.
        Gating on Claude Code's mere presence is the wrong axis -- it leaves every
        ``servers``-shaped file reported by nobody.
        This is also the only one of Visual Studio's five MCP locations that lives
        under the user's home, so it is the only one that survives
        ``filter_tool_projects_by_user`` on the conventional ``C:\src\...`` layout.
        """
        if self._user_scope_cache is not _UNSET:
            return self._user_scope_cache

        configs: List[Dict] = []

        def for_user(user_home: Path) -> None:
            mcp_json = user_home / USER_MCP_FILENAME
            if is_symlink_or_junction(mcp_json):
                return
            try:
                if not mcp_json.is_file():
                    return
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not stat {mcp_json}: {e}")
                return
            servers = _visual_studio_servers(mcp_json)
            if servers:
                configs.append({"path": str(user_home), "mcpServers": servers})
            else:
                logger.debug(f"No Visual Studio-shaped servers in {mcp_json}")

        try:
            scan_windows_user_directories(for_user)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error scanning user directories for Visual Studio MCP: {e}")
        self._user_scope_cache = configs
        return configs

    def _solution_configs(self) -> List[Dict]:
        r"""``<SOLUTIONDIR>\.vs\mcp.json`` found beneath the user home trees.

        Bounded to homes rather than the whole drive on purpose.
        ``filter_tool_projects_by_user`` keeps only projects under the scanned
        user's home, so a drive-wide walk spends its time on ``C:\Windows``,
        ``C:\Program Files`` and the like to produce rows that are discarded
        before upload. Measured on a CI runner with Visual Studio installed: 101
        seconds, zero rows kept. The output is identical either way.
        """
        if self._solutions_cache is not _UNSET:
            return self._solutions_cache

        configs: List[Dict] = []
        system_dirs = get_windows_system_directories()

        def for_user(user_home: Path) -> None:
            self._walk_for_solution_dirs(user_home, user_home, configs, system_dirs, 0)

        try:
            scan_windows_user_directories(for_user)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error scanning user directories for .vs configs: {e}")
        self._solutions_cache = configs
        return configs

    def _walk_for_solution_dirs(self, root: Path, current: Path, configs: List[Dict],
                                system_dirs: set, depth: int) -> None:
        """Recurse for ``.vs`` dirs. Never raises."""
        if depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current.iterdir():
                try:
                    # `.vs` is not in SKIP_DIRS, but its parents may be.
                    if item.name != VS_DIR_NAME and should_skip_path(item, system_dirs):
                        continue
                    if not item.is_dir() or is_symlink_or_junction(item):
                        continue
                    if item.name == VS_DIR_NAME:
                        config = self._read_solution_config(item)
                        if config:
                            configs.append(config)
                        continue
                    if item.name in SKIP_DIRS:
                        continue
                    self._walk_for_solution_dirs(root, item, configs, system_dirs, depth + 1)
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current}: {e}")

    def _read_solution_config(self, vs_dir: Path) -> Optional[Dict]:
        """Parse ``<vs_dir>/mcp.json``, keyed to the solution dir that owns it."""
        mcp_json = vs_dir / MCP_FILENAME
        if is_symlink_or_junction(mcp_json):
            return None
        if not mcp_json.is_file():
            return None
        return read_mcp_json(mcp_json, str(vs_dir.parent), "Visual Studio")
