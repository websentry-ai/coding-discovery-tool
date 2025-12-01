"""
MCP config extraction for Windsurf on macOS systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...macos_extraction_helpers import (
    extract_project_level_mcp_configs_with_fallback,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_windsurf_mcp_from_dir,
    walk_for_windsurf_mcp_configs,
    extract_global_mcp_config_with_root_support,
)

logger = logging.getLogger(__name__)


class MacOSWindsurfMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Windsurf MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Windsurf MCP configuration on macOS.
        
        Extracts both global and project-level MCP configs.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global config
        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)
        
        # Extract project-level configs
        project_configs = self._extract_project_level_configs()
        projects.extend(project_configs)
        
        # Return None if no configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_global_config(self) -> Optional[Dict]:
        """Extract global MCP config from ~/.codeium/windsurf/mcp_config.json"""
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Windsurf",
            parent_levels=3  # ~/.codeium/windsurf/mcp_config.json -> 3 levels up = ~
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """Extract project-level MCP configs from all .windsurf/mcp_config.json files"""
        root_path = Path("/")
        global_windsurf_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
        
        # Create a combined should_skip function for macOS
        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)
        
        return extract_project_level_mcp_configs_with_fallback(
            root_path,
            ".windsurf",
            global_windsurf_dir,
            extract_windsurf_mcp_from_dir,
            walk_for_windsurf_mcp_configs,
            should_skip
        )

