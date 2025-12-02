"""
MCP config extraction for Cline on Windows systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    read_ide_global_mcp_config,
)

logger = logging.getLogger(__name__)


class WindowsClineMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cline MCP config on Windows systems."""

    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"
    IDE_NAMES = ['Code', 'Cursor', 'Windsurf']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Cline MCP configuration on Windows.
        
        Extracts global MCP configs from IDE global storage.
        Cline stores MCP configs in cline_mcp_settings.json files.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global configs from all IDEs
        global_configs = self._extract_global_configs()
        projects.extend(global_configs)
        
        # Return None if no configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_global_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from IDE global storage for all IDEs.
        
        When running as administrator, collects global configs from ALL users.
        Returns list of configs found.
        """
        return extract_ide_global_configs_with_root_support(
            self._extract_global_configs_for_user,
            tool_name="Cline"
        )
    
    def _extract_global_configs_for_user(self, user_home: Path) -> List[Dict]:
        """
        Extract global MCP configs for a specific user from all IDEs.
        
        Args:
            user_home: User's home directory
            
        Returns:
            List of global config dicts
        """
        configs = []
        # Windows VS Code/Cursor/Windsurf global storage path
        code_base = user_home / "AppData" / "Roaming"
        
        # Check each IDE
        for ide_name in self.IDE_NAMES:
            # Try with settings subdirectory first (actual structure)
            config_path = (
                code_base / ide_name / "User" / "globalStorage" /
                self.CLINE_EXTENSION_ID / "settings" / "cline_mcp_settings.json"
            )
            if config_path.exists():
                config = self._read_global_config(config_path, ide_name)
                if config:
                    configs.append(config)
        
        return configs
    
    def _read_global_config(self, config_path: Path, ide_name: str) -> Optional[Dict]:
        """
        Read and parse a global MCP config file.
        
        Args:
            config_path: Path to the global config file
            ide_name: Name of the IDE (Code, Cursor, Windsurf) - unused but kept for compatibility
            
        Returns:
            Config dict or None
        """
        return read_ide_global_mcp_config(
            config_path,
            tool_name="Cline",
            use_full_path=True  # Cline uses full path including cline_mcp_settings.json
        )

