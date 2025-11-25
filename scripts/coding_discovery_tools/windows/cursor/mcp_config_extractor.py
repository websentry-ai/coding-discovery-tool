"""
MCP config extraction for Cursor on Windows systems.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...windows_extraction_helpers import should_skip_path
from ...mcp_extraction_helpers import (
    extract_cursor_mcp_from_dir,
    walk_for_cursor_mcp_configs,
    transform_mcp_servers_to_array,
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
        # When running as admin, prioritize checking user directories first
        is_admin = self._is_running_as_admin()
        users_dir = Path("C:\\Users")
        
        if is_admin and users_dir.exists():
            for user_dir in users_dir.iterdir():
                if user_dir.is_dir() and not user_dir.name.startswith('.'):
                    user_global_config = user_dir / ".cursor" / "mcp.json"
                    if user_global_config.exists():
                        config = self._read_global_config(user_global_config)
                        if config:
                            return config
            
            # Fallback to admin's own global config if no user config found
            if self.GLOBAL_MCP_CONFIG_PATH.exists():
                return self._read_global_config(self.GLOBAL_MCP_CONFIG_PATH)
        else:
            # For regular users, check their own home directory
            if self.GLOBAL_MCP_CONFIG_PATH.exists():
                return self._read_global_config(self.GLOBAL_MCP_CONFIG_PATH)
        
        return None
    
    def _is_running_as_admin(self) -> bool:
        """Check if running as administrator on Windows."""
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM (exact match only)
            try:
                import getpass
                current_user = getpass.getuser().lower()
                return current_user in ["administrator", "system"]
            except Exception:
                return False
    
    def _read_global_config(self, config_path: Path) -> Optional[Dict]:
        """Read and parse a global MCP config file."""
        try:
            content = config_path.read_text(encoding='utf-8', errors='replace')
            config_data = json.loads(content)
            
            mcp_servers_obj = config_data.get("mcpServers", {})
            
            # Transform mcpServers from object to array
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
            
            # Only return if there are MCP servers configured
            if mcp_servers_array:
                # Use the actual path of the global config file's parent directory (Cursor directory)
                global_config_path = str(config_path.parent)
                return {
                    "path": global_config_path,
                    "mcpServers": mcp_servers_array
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in global MCP config {config_path}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied reading global MCP config {config_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading global MCP config {config_path}: {e}")
        
        return None

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

