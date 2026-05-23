"""
MCP config extraction for Junie on Windows systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import read_global_mcp_config
from ...windows_extraction_helpers import scan_windows_user_directories

logger = logging.getLogger(__name__)

_TOOL_NAME = "Junie"
_PARENT_LEVELS = 2


class WindowsJunieMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Junie MCP config on Windows systems."""

    JUNIE_DIR_NAME = ".junie"
    MCP_CONFIG_SUBPATH = Path("mcp") / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """Extract Junie MCP configuration on Windows."""
        projects = self._extract_global_configs()
        return {"projects": projects} if projects else None

    def _extract_global_configs(self) -> List[Dict]:
        """Extract global MCP configs from ~\\.junie\\mcp\\mcp.json for each user.

        Uses the shared scan_windows_user_directories helper for consistent
        admin/non-admin branching and system-account exclusion.
        """
        configs: List[Dict] = []

        def collect_for_user(user_home: Path) -> None:
            config = self._extract_config_for_user(user_home)
            if config:
                configs.append(config)

        scan_windows_user_directories(collect_for_user)
        return configs

    def _extract_config_for_user(self, user_home: Path) -> Optional[Dict]:
        """Extract MCP config for a specific user."""
        config_path = user_home / self.JUNIE_DIR_NAME / self.MCP_CONFIG_SUBPATH

        if not config_path.exists():
            return None

        return read_global_mcp_config(
            config_path,
            tool_name=_TOOL_NAME,
            parent_levels=_PARENT_LEVELS,
        )
