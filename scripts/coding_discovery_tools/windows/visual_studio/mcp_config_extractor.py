r"""
MCP config extraction for GitHub Copilot in Visual Studio (Windows).

Visual Studio reads MCP servers from five locations
(learn.microsoft.com/visualstudio/ide/mcp-servers), but four of them are shared
with editors this tool already inventories:

    %USERPROFILE%\.mcp.json          -> Claude Code
    <SOLUTIONDIR>\.mcp.json          -> Claude Code / Copilot CLI
    <SOLUTIONDIR>\.vscode\mcp.json   -> GitHub Copilot (VS Code)
    <SOLUTIONDIR>\.cursor\mcp.json   -> Cursor
    <SOLUTIONDIR>\.vs\mcp.json       -> here

Visual Studio borrows the other editors' files on purpose, so claiming them would
duplicate every server already attributed elsewhere. Only ``.vs`` is read here.
A Visual Studio row therefore reports fewer servers than the IDE actually loads —
a deliberate under-report, not a miss.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import is_symlink_or_junction
from ...mcp_extraction_helpers import read_mcp_json
from ...windows_extraction_helpers import collect_workspace_config_dirs

logger = logging.getLogger(__name__)

MCP_FILENAME = "mcp.json"


class WindowsVisualStudioMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Visual Studio MCP config on Windows systems."""

    def extract_mcp_config(self, tool_name: Optional[str] = None) -> Optional[Dict]:
        """Solution-scoped ``<SOLUTIONDIR>\\.vs\\mcp.json`` servers, or None."""
        projects: List[Dict] = []
        # Shared walk: `.vs` and `.vscode` are collected in one pass over the drive.
        for vs_dir in collect_workspace_config_dirs().get(".vs", []):
            config = self._read_solution_config(vs_dir)
            if config:
                projects.append(config)

        if not projects:
            return None
        return {"projects": projects}

    def _read_solution_config(self, vs_dir: Path) -> Optional[Dict]:
        """Parse ``<vs_dir>/mcp.json``, keyed to the solution dir that owns it."""
        mcp_json = vs_dir / MCP_FILENAME
        if is_symlink_or_junction(mcp_json):
            return None
        if not mcp_json.is_file():
            return None
        return read_mcp_json(mcp_json, str(vs_dir.parent), "Visual Studio")
