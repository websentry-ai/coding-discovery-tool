r"""
MCP config extraction for GitHub Copilot in Visual Studio (Windows).

Visual Studio reads MCP servers from five locations
(learn.microsoft.com/visualstudio/ide/mcp-servers), but four of them are shared
with editors this tool already inventories:

    %USERPROFILE%\.mcp.json          -> here (Claude Code reads it but does not report it)
    <SOLUTIONDIR>\.mcp.json          -> Claude Code / Copilot CLI
    <SOLUTIONDIR>\.vscode\mcp.json   -> GitHub Copilot (VS Code)
    <SOLUTIONDIR>\.cursor\mcp.json   -> Cursor
    <SOLUTIONDIR>\.vs\mcp.json       -> here

Visual Studio borrows the other editors' files on purpose, so claiming them would
duplicate every server already attributed elsewhere rather than discover it.

``.vs\mcp.json`` is Visual Studio's alone. ``%USERPROFILE%\.mcp.json`` is claimed
because nothing else reports it — verified live against Claude Code's own
extractor — and it is the one Visual Studio MCP location under the user's home, so it is the only one
that survives ``filter_tool_projects_by_user`` on the conventional ``C:\src\...``
solution layout. A Visual Studio row still reports fewer servers than the IDE
actually loads: a deliberate under-report, not a miss.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import is_symlink_or_junction
from ...mcp_extraction_helpers import read_mcp_json
from ...windows_extraction_helpers import (
    collect_workspace_config_dirs,
    scan_windows_user_directories,
)

logger = logging.getLogger(__name__)

MCP_FILENAME = "mcp.json"
USER_MCP_FILENAME = ".mcp.json"


class WindowsVisualStudioMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Visual Studio MCP config on Windows systems."""

    def extract_mcp_config(self, tool_name: Optional[str] = None) -> Optional[Dict]:
        """Solution-scoped ``.vs\\mcp.json`` servers plus ``%USERPROFILE%\\.mcp.json``."""
        projects: List[Dict] = []
        # Shared walk: `.vs` and `.vscode` are collected in one pass over the drive.
        for vs_dir in collect_workspace_config_dirs().get(".vs", []):
            config = self._read_solution_config(vs_dir)
            if config:
                projects.append(config)

        projects.extend(self._user_scope_configs())

        if not projects:
            return None
        return {"projects": projects}

    def _user_scope_configs(self) -> List[Dict]:
        r"""``%USERPROFILE%\.mcp.json`` — what Visual Studio calls its *global* MCP
        server configuration.

        Claimed unconditionally. Claude Code reads this path too, but measured on a
        live Windows box with Claude Code installed its extractor returns
        ``~/.claude.json`` and not the home-rooted ``.mcp.json`` -- so gating on
        Claude Code's presence left the file reported by nobody at all.
        This is also the only one of Visual Studio's five MCP locations that lives
        under the user's home, so it is the only one that survives
        ``filter_tool_projects_by_user`` on the conventional ``C:\src\...`` layout.
        """
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
            config = read_mcp_json(mcp_json, str(user_home), "Visual Studio")
            if config:
                configs.append(config)

        try:
            scan_windows_user_directories(for_user)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error scanning user directories for Visual Studio MCP: {e}")
        return configs

    def _read_solution_config(self, vs_dir: Path) -> Optional[Dict]:
        """Parse ``<vs_dir>/mcp.json``, keyed to the solution dir that owns it."""
        mcp_json = vs_dir / MCP_FILENAME
        if is_symlink_or_junction(mcp_json):
            return None
        if not mcp_json.is_file():
            return None
        return read_mcp_json(mcp_json, str(vs_dir.parent), "Visual Studio")
