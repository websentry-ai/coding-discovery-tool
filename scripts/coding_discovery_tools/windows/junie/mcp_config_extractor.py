"""
MCP config extraction for Junie on Windows systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import read_global_mcp_config
from ...windows_extraction_helpers import is_running_as_admin

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
        """Extract global MCP configs from ~\\.junie\\mcp\\mcp.json for each user."""
        configs: List[Dict] = []

        if is_running_as_admin():
            users_dir = Path(Path.home().anchor) / "Users"
            if users_dir.exists():
                excluded = {'public', 'default', 'default user', 'all users'}
                for user_dir in users_dir.iterdir():
                    if not user_dir.is_dir() or user_dir.name.startswith('.'):
                        continue
                    if user_dir.name.lower() in excluded:
                        continue
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
        """Extract MCP config for a specific user."""
        config_path = user_home / self.JUNIE_DIR_NAME / self.MCP_CONFIG_SUBPATH

        if not config_path.exists():
            return None

        return read_global_mcp_config(
            config_path,
            tool_name=_TOOL_NAME,
            parent_levels=_PARENT_LEVELS,
        )
