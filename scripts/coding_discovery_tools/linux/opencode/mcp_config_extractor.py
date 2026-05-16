"""MCP config extraction for OpenCode on Linux systems."""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.opencode.mcp_config_extractor import read_opencode_mcp_config

logger = logging.getLogger(__name__)


class LinuxOpenCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for OpenCode MCP config on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = self._extract_global_configs()
        return {"projects": projects} if projects else None

    def _extract_global_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            config_path = user_home / ".config" / "opencode" / "opencode.json"
            if config_path.exists():
                config = read_opencode_mcp_config(config_path, tool_name="OpenCode", parent_levels=3)
                if config:
                    configs.append(config)
        return configs
