"""
MCP config extraction for Antigravity on macOS systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import extract_global_mcp_config_with_root_support

logger = logging.getLogger(__name__)


class MacOSAntigravityMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Antigravity MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".gemini" / "antigravity" / "mcp_config.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Antigravity MCP configuration on macOS.
        
        Extracts global MCP config from ~/.gemini/antigravity/mcp_config.json
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []

        # Extract global config(s) — one per user when running as root
        projects.extend(self._extract_global_config())

        # Return None if no configs found
        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_global_config(self) -> List[Dict]:
        """
        Extract global MCP config from ~/.gemini/antigravity/mcp_config.json

        When running as root, collects global configs from ALL users.
        """
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Antigravity",
            parent_levels=3  # ~/.gemini/antigravity/mcp_config.json -> 3 levels up = ~
        )

