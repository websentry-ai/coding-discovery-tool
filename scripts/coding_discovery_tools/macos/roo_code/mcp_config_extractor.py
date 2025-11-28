"""
MCP config extraction for Roo Code on macOS systems.
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
    extract_roo_mcp_from_dir,
    walk_for_roo_mcp_configs,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)


class MacOSRooMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Roo Code MCP config on macOS systems."""

    # Code base global storage paths for different IDEs
    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"
    IDE_NAMES = ['Code', 'Cursor', 'Windsurf']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Roo Code MCP configuration on macOS.
        
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
        
        When running as root, collects global configs from ALL users.
        Returns list of configs found.
        """
        all_configs = []
        
        # When running as root, check all users
        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        user_configs = self._extract_global_configs_for_user(user_dir)
                        all_configs.extend(user_configs)
            
            # Also check root's configs
            root_configs = self._extract_global_configs_for_user(Path.home())
            all_configs.extend(root_configs)
        else:
            # For regular users, check their own home directory
            user_configs = self._extract_global_configs_for_user(Path.home())
            all_configs.extend(user_configs)
        
        return all_configs
    
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
            config_path = code_base / ide_name / "User" / "globalStorage" / self.ROO_EXTENSION_ID / "settings" / "mcp_settings.json"
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
            ide_name: Name of the IDE (Code, Cursor, Windsurf)
            
        Returns:
            Config dict or None
        """
        try:
            content = config_path.read_text(encoding='utf-8', errors='replace')
            config_data = json.loads(content)
            
            mcp_servers_obj = config_data.get("mcpServers", {})
            
            # Transform mcpServers from object to array
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
            
            # Only return if there are MCP servers configured
            if mcp_servers_array:
                # Use the full path including mcp_settings.json
                return {
                    "path": str(config_path),
                    "mcpServers": mcp_servers_array
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in global Roo MCP config {config_path}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied reading global Roo MCP config {config_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading global Roo MCP config {config_path}: {e}")
        
        return None

    def _extract_project_level_configs(self) -> List[Dict]:
        """Extract project-level MCP configs from all .roo/mcp.json files"""
        projects = []
        root_path = Path("/")
        
        try:
            # Get top-level directories, skipping system ones
            top_level_dirs = get_top_level_directories(root_path)
            
            # Search each top-level directory
            # No global .roo directory to skip (unlike .cursor)
            
            # Create a combined should_skip function for macOS
            def should_skip(item: Path) -> bool:
                return should_skip_path(item) or should_skip_system_path(item)
            
            for top_dir in top_level_dirs:
                try:
                    walk_for_roo_mcp_configs(
                        root_path, top_dir, projects, None,
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
            
            def should_skip(item: Path) -> bool:
                return should_skip_path(item) or should_skip_system_path(item)
            
            for roo_dir in home_path.rglob(".roo"):
                try:
                    if not should_process_directory(roo_dir, home_path):
                        continue
                    extract_roo_mcp_from_dir(roo_dir, projects, None)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {roo_dir}: {e}")
                    continue
        
        return projects

