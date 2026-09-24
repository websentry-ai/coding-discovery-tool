"""
MCP config extraction for Muse Code on macOS systems.

Muse Code keeps user MCP servers under ``mcpServers`` in
``~/.config/muse/settings.json`` (``{"schema_version": 1, "mcpServers": {...}}``).
The project-level ``.mcp.json`` it also reads is shared with Claude Code and is
reported there.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import extract_global_mcp_config_with_root_support

logger = logging.getLogger(__name__)


class MacOSMuseCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Muse Code MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".config" / "muse" / "settings.json"

    def extract_mcp_config(self, plugin_lookup=None) -> Optional[Dict]:
        """
        Extract Muse Code MCP servers from ~/.config/muse/settings.json.

        When running as root, collects the config of every user.

        Returns:
            Dict with a projects array, or None when no servers are configured.
        """
        projects = extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Muse Code",
            parent_levels=3,  # ~/.config/muse/settings.json -> ~
        )
        if not projects:
            return None
        return {"projects": projects}
