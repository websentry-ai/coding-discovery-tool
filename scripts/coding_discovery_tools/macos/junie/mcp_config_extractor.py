"""
MCP config extraction for Junie on macOS systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    read_global_mcp_config,
)
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)

_TOOL_NAME = "Junie"
_PARENT_LEVELS = 2


class MacOSJunieMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Junie MCP config on macOS systems."""

    JUNIE_DIR_NAME = ".junie"
    MCP_CONFIG_SUBPATH = Path("mcp") / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Junie MCP configuration on macOS.
        """
        projects = []

        # Extract global configs
        global_configs = self._extract_global_configs()
        projects.extend(global_configs)

        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_global_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from ~/.junie/mcp/mcp.json.
        """
        configs = []

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            config = self._extract_config_for_user(user_dir)
                            if config:
                                configs.append(config)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            config = self._extract_config_for_user(Path.home())
            if config:
                configs.append(config)

        return configs

    def _extract_config_for_user(self, user_home: Path) -> Optional[Dict]:
        """
        Extract MCP config for a specific user.
        """
        config_path = user_home / self.JUNIE_DIR_NAME / self.MCP_CONFIG_SUBPATH

        if not config_path.exists():
            return None

        return read_global_mcp_config(
            config_path,
            tool_name=_TOOL_NAME,
            parent_levels=_PARENT_LEVELS
        )
