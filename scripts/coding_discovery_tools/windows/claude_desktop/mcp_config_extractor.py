"""
MCP config extraction for Claude Desktop on Windows systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import extract_global_mcp_config_with_root_support
from .claude_desktop import claude_data_dir

logger = logging.getLogger(__name__)


class WindowsClaudeDesktopMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Claude Desktop MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = claude_data_dir(Path.home()) / "claude_desktop_config.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Claude Desktop MCP configuration on Windows.

        Extracts global MCP config from %APPDATA%/Claude/claude_desktop_config.json

        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = self._extract_global_config()

        if not projects:
            logger.debug("No Claude Desktop MCP config at %s", self.GLOBAL_MCP_CONFIG_PATH)
            return None

        return {
            "projects": projects
        }

    def _extract_global_config(self) -> List[Dict]:
        """
        Extract global MCP config from Claude Desktop's data directory.

        When running as administrator, collects global configs from ALL users.
        """
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Claude Desktop",
            parent_levels=4  # ~/AppData/Roaming/Claude/<file> -> 4 levels up = ~
        )
