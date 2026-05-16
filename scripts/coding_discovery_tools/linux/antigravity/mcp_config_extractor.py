"""MCP config extraction for Antigravity on Linux systems."""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import get_linux_user_homes
from ...mcp_extraction_helpers import extract_global_mcp_config_with_root_support

logger = logging.getLogger(__name__)


class LinuxAntigravityMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Antigravity MCP config on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = []

        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

        return {"projects": projects} if projects else None

    def _extract_global_config(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            config_path = user_home / ".gemini" / "antigravity" / "mcp_config.json"
            if config_path.exists():
                config = extract_global_mcp_config_with_root_support(
                    config_path,
                    tool_name="Antigravity",
                    parent_levels=3,
                )
                if config:
                    return config
        return None
