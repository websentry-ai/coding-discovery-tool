"""
MCP config extraction for Roo Code on Windows systems.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_roo_mcp_from_dir,
    walk_for_roo_mcp_configs,
    extract_ide_global_configs_with_root_support,
    read_ide_global_mcp_config,
    extract_project_level_mcp_configs_with_fallback_windows,
)
from ...windows_extraction_helpers import should_skip_path

logger = logging.getLogger(__name__)


class WindowsRooMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Roo Code MCP config on Windows systems."""

    # Roo Code extension identifier
    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"
    IDE_NAMES = ['Code', 'Cursor', 'Windsurf']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Roo Code MCP configuration on Windows.
        
        Extracts both global and project-level MCP configs.
        Global configs are stored in code base global storage for different IDEs (Code, Cursor, Windsurf).
        Project-level configs are in .roo/mcp.json files.
        
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
        
        When running as administrator, collects global configs from ALL users.
        Returns list of configs found.
        """
        return extract_ide_global_configs_with_root_support(
            self._extract_global_configs_for_user,
            tool_name="Roo Code"
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
            config_path = (
                code_base / ide_name / "User" / "globalStorage" /
                self.ROO_EXTENSION_ID / "settings" / "mcp_settings.json"
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
            tool_name="Roo Code",
            use_full_path=True  # Roo uses full path including mcp_settings.json
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """
        Extract project-level MCP configs from all .roo/mcp.json files.
        
        Uses Windows-specific implementation with proper system directory skipping.
        """
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        # Create a should_skip function for Windows
        def should_skip(item: Path) -> bool:
            system_dirs = self._get_system_directories()
            return should_skip_path(item, system_dirs)
        
        return extract_project_level_mcp_configs_with_fallback_windows(
            root_path,
            ".roo",
            None,  # No global directory to skip
            extract_roo_mcp_from_dir,
            walk_for_roo_mcp_configs,
            should_skip
        )

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.
        
        Returns:
            Set of system directory names
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }

