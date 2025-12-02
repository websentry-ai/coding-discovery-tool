"""
MCP config extraction for Kilo Code on macOS systems.
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
    extract_kilocode_mcp_from_dir,
    walk_for_kilocode_mcp_configs,
    extract_ide_global_configs_with_root_support,
    read_ide_global_mcp_config,
)

logger = logging.getLogger(__name__)


class MacOSKiloCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Kilo Code MCP config on macOS systems."""

    # Code base global storage paths for different IDEs
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"
    IDE_NAMES = ['Code', 'Cursor', 'Windsurf', 'Antigravity']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Kilo Code MCP configuration on macOS.
        
        Extracts both global and project-level MCP configs.
        Global configs are stored in code base global storage for different IDEs (Code, Cursor, Windsurf, Antigravity).
        Project-level configs are in .kilocode/mcp.json files.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global configs from all IDEs
        global_configs = self._extract_global_configs()
        projects.extend(global_configs)
        
        # Extract project-level configs
        project_configs = self._extract_project_level_configs()
        projects.extend(project_configs)
        
        # Return None if no configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_global_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from code base global storage for all IDEs.
        
        When running as root, collects global configs from ALL users.
        Returns list of configs found.
        """
        return extract_ide_global_configs_with_root_support(
            self._extract_global_configs_for_user,
            tool_name="Kilo Code"
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
        code_base = user_home / "Library" / "Application Support"
        
        # Check each IDE
        for ide_name in self.IDE_NAMES:
            # Try with settings subdirectory first (actual structure)
            config_path = code_base / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID / "settings" / "mcp_settings.json"
            if not config_path.exists():
                # Fallback to direct path (for compatibility)
                config_path = code_base / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID / "mcp_settings.json"
            
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
            ide_name: Name of the IDE (Code, Cursor) - unused but kept for compatibility
            
        Returns:
            Config dict or None
        """
        return read_ide_global_mcp_config(
            config_path,
            tool_name="Kilo Code",
            use_full_path=True  # Kilo Code uses full path including mcp_settings.json
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """Extract project-level MCP configs from all .kilocode/mcp.json files"""
        root_path = Path("/")
        # No global .kilocode directory to skip (unlike .cursor)
        
        # Create a combined should_skip function for macOS
        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)
        
        return extract_project_level_mcp_configs_with_fallback(
            root_path,
            ".kilocode",
            None,  # No global directory to skip
            extract_kilocode_mcp_from_dir,
            walk_for_kilocode_mcp_configs,
            should_skip
        )

