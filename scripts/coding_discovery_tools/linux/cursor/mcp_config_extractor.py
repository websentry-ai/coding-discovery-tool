"""MCP config extraction for Cursor on Linux."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    walk_for_cursor_mcp_configs,
    read_global_mcp_config,
)

logger = logging.getLogger(__name__)


class LinuxCursorMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cursor MCP config on Linux systems."""

    _GLOBAL_MCP_RELATIVE = Path(".cursor") / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        projects: List[Dict] = []

        for user_home in get_linux_user_homes():
            user_global_path = user_home / self._GLOBAL_MCP_RELATIVE
            if user_global_path.exists():
                global_config = read_global_mcp_config(user_global_path, tool_name="Cursor", parent_levels=2)
                if global_config:
                    projects.append(global_config)

        projects.extend(self._extract_project_level_configs())

        return {"projects": projects} if projects else None

    def _extract_project_level_configs(self) -> List[Dict]:
        projects: List[Dict] = []

        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)

        for user_home in get_linux_user_homes():
            global_cursor_dir = user_home / ".cursor"
            try:
                walk_for_cursor_mcp_configs(
                    user_home, user_home, projects, global_cursor_dir,
                    should_skip, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return projects
