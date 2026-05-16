"""MCP config extraction for Cursor CLI on Linux systems."""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_cursor_mcp_from_dir,
    walk_for_cursor_mcp_configs,
)

logger = logging.getLogger(__name__)


class LinuxCursorCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cursor CLI MCP config on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = []
        projects.extend(self._extract_global_configs())
        projects.extend(self._extract_project_level_configs())
        return {"projects": projects} if projects else None

    def _extract_global_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            global_mcp = user_home / ".cursor" / "mcp.json"
            if global_mcp.exists():
                user_configs: List[Dict] = []
                extract_cursor_mcp_from_dir(global_mcp.parent, user_configs, global_cursor_dir=None)
                configs.extend(user_configs)
        return configs

    def _extract_project_level_configs(self) -> List[Dict]:
        configs = []

        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)

        for user_home in get_linux_user_homes():
            global_cursor_dir = user_home / ".cursor"
            try:
                walk_for_cursor_mcp_configs(
                    user_home, user_home, configs, global_cursor_dir,
                    should_skip, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return configs
