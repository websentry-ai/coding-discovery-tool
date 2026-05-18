"""MCP config extraction for Roo Code on Linux systems."""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_roo_mcp_from_dir,
    walk_for_roo_mcp_configs,
    read_ide_global_mcp_config,
)

logger = logging.getLogger(__name__)


class LinuxRooMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Roo Code MCP config on Linux systems."""

    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"
    IDE_NAMES = ["Code", "Cursor", "Windsurf"]

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = []
        projects.extend(self._extract_global_configs())
        projects.extend(self._extract_project_level_configs())
        return {"projects": projects} if projects else None

    def _extract_global_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            configs.extend(self._extract_global_configs_for_user(user_home))
        return configs

    def _extract_global_configs_for_user(self, user_home: Path) -> List[Dict]:
        configs = []
        for ide_name in self.IDE_NAMES:
            config_path = (
                user_home / ".config" / ide_name / "User" / "globalStorage"
                / self.ROO_EXTENSION_ID / "settings" / "mcp_settings.json"
            )
            if config_path.exists():
                config = read_ide_global_mcp_config(
                    config_path, tool_name="Roo Code", use_full_path=True
                )
                if config:
                    configs.append(config)
        return configs

    def _extract_project_level_configs(self) -> List[Dict]:
        configs = []

        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)

        for user_home in get_linux_user_homes():
            try:
                walk_for_roo_mcp_configs(
                    user_home, user_home, configs, None,
                    should_skip, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return configs
