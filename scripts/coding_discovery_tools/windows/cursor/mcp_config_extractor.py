"""
MCP config extraction for Cursor on Windows systems.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...windows_extraction_helpers import should_skip_path
from ...mcp_extraction_helpers import (
    extract_cursor_mcp_from_dir,
    walk_for_cursor_mcp_configs,
    extract_global_mcp_config_with_root_support,
)

logger = logging.getLogger(__name__)


class WindowsCursorMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cursor MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".cursor" / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Cursor MCP configuration on Windows.
        
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
        """
        Extract global MCP config from ~/.cursor/mcp.json
        
        When running as admin, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        # Note: Windows uses parent_levels=1 because path is ~/.cursor/mcp.json
        # and we want ~/.cursor as the path (not ~)
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Cursor",
            parent_levels=1  # ~/.cursor/mcp.json -> 1 level up = ~/.cursor
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """
        Extract project-level MCP configs from all .cursor/mcp.json files.
        
        Uses parallel processing for top-level directories to improve performance.
        """
        projects = []
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Use parallel processing for top-level directories
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(
                        self._walk_for_cursor_mcp_configs,
                        root_path, dir_path, global_cursor_dir, current_depth=1
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
                global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
                walk_for_cursor_mcp_configs(
                    root_path, root_path, projects, global_cursor_dir,
                    should_skip_path, current_depth=0
                )
            except (PermissionError, OSError) as fallback_error:
                logger.warning(f"Error in fallback processing: {fallback_error}")
                # Final fallback to home directory
                logger.info("Falling back to home directory search")
                home_path = Path.home()
                global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
                
                for cursor_dir in home_path.rglob(".cursor"):
                    try:
                        extract_cursor_mcp_from_dir(cursor_dir, projects, global_cursor_dir)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {cursor_dir}: {e}")
                        continue
        
        return projects
    
    def _walk_for_cursor_mcp_configs(
        self,
        root_path: Path,
        current_dir: Path,
        global_cursor_dir: Path,
        current_depth: int = 0
    ) -> List[Dict]:
        """
        Walk directory tree looking for .cursor/mcp.json files.
        
        This method collects results in a local list for thread safety.
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            global_cursor_dir: Path to global .cursor directory to skip
            current_depth: Current recursion depth
            
        Returns:
            List of project config dicts found in this directory tree
        """
        projects = []
        system_dirs = self._get_system_directories()
        
        def should_skip(item: Path) -> bool:
            return should_skip_path(item, system_dirs)
        
        walk_for_cursor_mcp_configs(
            root_path, current_dir, projects, global_cursor_dir,
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

