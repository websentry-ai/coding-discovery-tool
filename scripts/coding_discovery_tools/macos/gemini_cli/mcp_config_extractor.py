"""
MCP config extraction for Gemini CLI on macOS systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_global_mcp_config_with_root_support,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)


class MacOSGeminiCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Gemini CLI MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".gemini" / "settings.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Gemini CLI MCP configuration on macOS.
        
        Extracts global MCP config from ~/.gemini/settings.json.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global config
        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)
        
        # Return None if no configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_global_config(self) -> Optional[Dict]:
        """
        Extract global MCP config from ~/.gemini/settings.json
        
        When running as root, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Gemini CLI",
            parent_levels=2  # ~/.gemini/settings.json -> 2 levels up = ~
        )

