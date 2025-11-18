"""
MCP config extraction for Cursor on Windows systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...windows_extraction_helpers import should_skip_path
from ...mcp_extraction_helpers import (
    extract_cursor_mcp_from_dir,
    walk_for_cursor_mcp_configs,
)

logger = logging.getLogger(__name__)


class WindowsCursorMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cursor MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / "AppData" / "Roaming" / "Cursor" / "mcp.json"

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
        """Extract global MCP config from %APPDATA%\Cursor\mcp.json"""
        if not self.GLOBAL_MCP_CONFIG_PATH.exists():
            return None
        
        try:
            content = self.GLOBAL_MCP_CONFIG_PATH.read_text(encoding='utf-8', errors='replace')
            config_data = json.loads(content)
            
            mcp_servers = config_data.get("mcpServers", {})
            
            # Only return if there are MCP servers configured
            if mcp_servers:
                # Use the actual path of the global config file's parent directory
                global_config_path = str(self.GLOBAL_MCP_CONFIG_PATH.parent.parent)
                return {
                    "path": global_config_path,
                    "mcpServers": mcp_servers
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in global MCP config {self.GLOBAL_MCP_CONFIG_PATH}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied reading global MCP config {self.GLOBAL_MCP_CONFIG_PATH}: {e}")
        except Exception as e:
            logger.warning(f"Error reading global MCP config {self.GLOBAL_MCP_CONFIG_PATH}: {e}")
        
        return None

    def _extract_project_level_configs(self) -> List[Dict]:
        """Extract project-level MCP configs from all .cursor/mcp.json files"""
        projects = []
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            global_cursor_dir = self.GLOBAL_MCP_CONFIG_PATH.parent
            walk_for_cursor_mcp_configs(
                root_path, root_path, projects, global_cursor_dir,
                should_skip_path, current_depth=0
            )
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to home directory
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

