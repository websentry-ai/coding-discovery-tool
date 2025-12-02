"""
MCP config extraction for Kilo Code on Windows systems.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...windows_extraction_helpers import should_skip_path
from ...mcp_extraction_helpers import (
    extract_kilocode_mcp_from_dir,
    walk_for_kilocode_mcp_configs,
    extract_ide_global_configs_with_root_support,
    read_ide_global_mcp_config,
)

logger = logging.getLogger(__name__)


class WindowsKiloCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Kilo Code MCP config on Windows systems."""

    # Code base global storage paths for different IDEs
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"
    IDE_NAMES = ['Code', 'Cursor']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Kilo Code MCP configuration on Windows.
        
        Extracts both global and project-level MCP configs.
        Global configs are stored in code base global storage for different IDEs (Code, Cursor).
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
        
        When running as administrator, collects global configs from ALL users.
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
        # Windows VS Code/Cursor global storage path
        code_base = user_home / "AppData" / "Roaming"
        
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
        """
        Extract project-level MCP configs from all .kilocode/mcp.json files.
        
        Uses parallel processing for top-level directories to improve performance.
        """
        projects = []
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            # No global .kilocode directory to skip (unlike .cursor)
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Use parallel processing for top-level directories
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(
                        self._walk_for_kilocode_mcp_configs,
                        root_path, dir_path, current_depth=1
                    )
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        dir_projects = future.result()
                        projects.extend(dir_projects)
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to sequential processing
            try:
                def should_skip(item: Path) -> bool:
                    return should_skip_path(item, self._get_system_directories())
                
                walk_for_kilocode_mcp_configs(
                    root_path, root_path, projects, None,  # No global directory to skip
                    should_skip, current_depth=0
                )
            except (PermissionError, OSError) as fallback_error:
                logger.warning(f"Error in fallback processing: {fallback_error}")
                # Final fallback to home directory
                logger.info("Falling back to home directory search")
                home_path = Path.home()
                
                for kilocode_dir in home_path.rglob(".kilocode"):
                    try:
                        extract_kilocode_mcp_from_dir(kilocode_dir, projects, None)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {kilocode_dir}: {e}")
                        continue
        
        return projects
    
    def _walk_for_kilocode_mcp_configs(
        self,
        root_path: Path,
        current_dir: Path,
        current_depth: int = 0
    ) -> List[Dict]:
        """
        Walk directory tree looking for .kilocode/mcp.json files.
        
        This method collects results in a local list for thread safety.
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            current_depth: Current recursion depth
            
        Returns:
            List of project config dicts found in this directory tree
        """
        projects = []
        system_dirs = self._get_system_directories()
        
        def should_skip(item: Path) -> bool:
            return should_skip_path(item, system_dirs)
        
        walk_for_kilocode_mcp_configs(
            root_path, current_dir, projects, None,  # No global directory to skip
            should_skip, current_depth
        )
        
        return projects
    
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

