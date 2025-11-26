"""
MCP config extraction for Cursor on macOS systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...macos_extraction_helpers import (
    get_top_level_directories,
    is_running_as_root,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_cursor_mcp_from_dir,
    walk_for_cursor_mcp_configs,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)


class MacOSCursorMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cursor MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".cursor" / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Cursor MCP configuration on macOS.
        
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
        
        When running as root, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        # When running as root, prioritize checking user directories first
        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        user_global_config = user_dir / ".cursor" / "mcp.json"
                        if user_global_config.exists():
                            config = self._read_global_config(user_global_config)
                            if config:
                                return config
            
            # Fallback to root's global config if no user config found (only if file exists)
            if self.GLOBAL_MCP_CONFIG_PATH.exists():
                return self._read_global_config(self.GLOBAL_MCP_CONFIG_PATH)
        else:
            # For regular users, check their own home directory
            if self.GLOBAL_MCP_CONFIG_PATH.exists():
                return self._read_global_config(self.GLOBAL_MCP_CONFIG_PATH)
        
        return None
    
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
                # Use the actual path of the global config file's parent directory
                global_config_path = str(config_path.parent.parent)
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
        """Extract project-level MCP configs from all .cursor/mcp.json files"""
        projects = []
        root_path = Path("/")
        
        try:
            # Get top-level directories, skipping system ones
            top_level_dirs = get_top_level_directories(root_path)
            
            # Search each top-level directory
            global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
            
            # Create a combined should_skip function for macOS
            def should_skip(item: Path) -> bool:
                return should_skip_path(item) or should_skip_system_path(item)
            
            for top_dir in top_level_dirs:
                try:
                    walk_for_cursor_mcp_configs(
                        root_path, top_dir, projects, global_cursor_dir,
                        should_skip, current_depth=1
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to home directory
            logger.info("Falling back to home directory search")
            home_path = Path.home()
            global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
            
            def should_skip(item: Path) -> bool:
                return should_skip_path(item) or should_skip_system_path(item)
            
            for cursor_dir in home_path.rglob(".cursor"):
                try:
                    if not should_process_directory(cursor_dir, home_path):
                        continue
                    extract_cursor_mcp_from_dir(cursor_dir, projects, global_cursor_dir)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {cursor_dir}: {e}")
                    continue
        
        return projects

