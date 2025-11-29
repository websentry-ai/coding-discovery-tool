"""
MCP config extraction for Cline on macOS systems.
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
from ...mcp_extraction_helpers import transform_mcp_servers_to_array

logger = logging.getLogger(__name__)


def extract_cline_mcp_from_dir(
    cline_dir: Path,
    projects: List[Dict],
    global_cline_dir: Optional[Path] = None
) -> None:
    """
    Extract MCP config from a .clinerules directory if cline_mcp_settings.json exists.
    
    Note: Cline MCP configs are stored in IDE global storage, not in .clinerules.
    This function is kept for consistency but may not be used.
    
    Args:
        cline_dir: Path to .clinerules directory
        projects: List to append project configs to
        global_cline_dir: Path to global .clinerules directory to skip (optional)
    """
    # Cline MCP configs are in IDE global storage, not in .clinerules
    # This function is a placeholder for consistency
    pass


def walk_for_cline_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_cline_dir: Optional[Path],
    should_skip_func,
    current_depth: int = 0
) -> None:
    """
    Walk directory tree looking for Cline MCP configs.
    
    Note: Cline MCP configs are stored in IDE global storage, not in project directories.
    This function is kept for consistency but may not be used.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_cline_dir: Path to global directory to skip (optional)
        should_skip_func: Function to determine if a path should be skipped
        current_depth: Current recursion depth
    """
    # Cline MCP configs are in IDE global storage, not in project directories
    # This function is a placeholder for consistency
    pass


class MacOSClineMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cline MCP config on macOS systems."""

    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"
    IDE_NAMES = ['Code', 'Cursor', 'Windsurf']

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Cline MCP configuration on macOS.
        
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
                # Use the full path including cline_mcp_settings.json
                return {
                    "path": str(config_path),
                    "mcpServers": mcp_servers_array
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in global Cline MCP config {config_path}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied reading global Cline MCP config {config_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading global Cline MCP config {config_path}: {e}")
        
        return None

