"""MCP config extraction for Cline on Linux systems."""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import get_linux_user_homes
from ...mcp_extraction_helpers import read_ide_global_mcp_config

logger = logging.getLogger(__name__)


class LinuxClineMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cline MCP config on Linux systems."""

    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"
    IDE_NAMES = ["Code", "Cursor", "Windsurf"]

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = self._extract_global_configs()
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
                / self.CLINE_EXTENSION_ID / "settings" / "cline_mcp_settings.json"
            )
            if config_path.exists():
                config = read_ide_global_mcp_config(
                    config_path, tool_name="Cline", use_full_path=True
                )
                if config:
                    configs.append(config)
        return configs
