"""
MCP config extraction for Junie on Linux systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import get_linux_user_homes
from ...mcp_extraction_helpers import read_global_mcp_config

logger = logging.getLogger(__name__)

_TOOL_NAME = "Junie"
# ~/.junie/mcp/mcp.json -> 3 levels up = ~ (home), matching how every other
# global MCP config keys its `path` to the user's home directory.
_PARENT_LEVELS = 3


class LinuxJunieMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Junie MCP config on Linux systems."""

    JUNIE_DIR_NAME = ".junie"
    MCP_CONFIG_SUBPATH = Path("mcp") / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """Extract Junie MCP configuration on Linux."""
        projects = self._extract_global_configs()
        return {"projects": projects} if projects else None

    def _extract_global_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from ~/.junie/mcp/mcp.json for every user.

        The outer loop already walks each user home, so we read each user's
        config directly rather than delegating to a root-support wrapper that
        re-iterates /home and could return another user's config.
        """
        configs: List[Dict] = []
        for user_home in get_linux_user_homes():
            config_path = user_home / self.JUNIE_DIR_NAME / self.MCP_CONFIG_SUBPATH
            if config_path.exists():
                config = read_global_mcp_config(
                    config_path,
                    tool_name=_TOOL_NAME,
                    parent_levels=_PARENT_LEVELS,
                )
                if config:
                    configs.append(config)
        return configs
