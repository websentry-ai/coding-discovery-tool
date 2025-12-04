"""
MCP config extraction for OpenCode on Windows systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_global_mcp_config_with_root_support,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)


def read_opencode_mcp_config(
    config_path: Path,
    tool_name: str = "OpenCode",
    parent_levels: int = 5
) -> Optional[Dict]:
    """
    Read and parse OpenCode JSON config file to extract MCP servers.
    
    Checks for MCP config in "mcp" section first, then falls back to root "mcpServers".
    
    Args:
        config_path: Path to the opencode.json file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
                      For AppData\Roaming\.config\opencode\opencode.json -> 5 levels up = home
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        # Check for MCP config in "mcp" section first (as per macOS implementation)
        mcp_servers_obj = None
        if "mcp" in config_data and isinstance(config_data["mcp"], dict):
            mcp_servers_obj = config_data["mcp"].get("mcpServers", {})
        
        # Fallback to root-level mcpServers if not found in "mcp" section
        if not mcp_servers_obj:
            mcp_servers_obj = config_data.get("mcpServers", {})
        
        if not mcp_servers_obj:
            return None
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only return if there are MCP servers configured
        if mcp_servers_array:
            # Calculate the global config path by going up parent_levels
            global_config_path = config_path
            for _ in range(parent_levels):
                global_config_path = global_config_path.parent
            return {
                "path": str(global_config_path),
                "mcpServers": mcp_servers_array
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in global {tool_name} MCP config {config_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_opencode_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = "OpenCode",
    parent_levels: int = 5
) -> Optional[Dict]:
    """
    Extract global OpenCode MCP config with support for admin user.
    
    Reuses the pattern from extract_global_mcp_config_with_root_support
    but calls read_opencode_mcp_config for JSON parsing with "mcp" section support.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    # When running as administrator, check all user directories
    if _is_running_as_admin():
        users_dir = Path("C:\\Users")
        if users_dir.exists():
            for user_dir in users_dir.iterdir():
                if user_dir.is_dir() and not user_dir.name.startswith('.'):
                    try:
                        user_config_path = user_dir / global_config_path.relative_to(Path.home())
                        if user_config_path.exists():
                            config = read_opencode_mcp_config(user_config_path, tool_name, parent_levels)
                            if config:
                                return config
                    except (ValueError, OSError):
                        continue
    
    # Fallback to current user's config
    if global_config_path.exists():
        return read_opencode_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


def _is_running_as_admin() -> bool:
    """
    Check if the current process is running as administrator.
    
    Returns:
        True if running as administrator, False otherwise
    """
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        # Fallback: check if current user is Administrator or SYSTEM
        try:
            import getpass
            current_user = getpass.getuser().lower()
            return current_user in ["administrator", "system"]
        except Exception:
            return False


class WindowsOpenCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for OpenCode MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / "AppData" / "Roaming" / ".config" / "opencode" / "opencode.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract OpenCode MCP configuration on Windows.
        
        Extracts global MCP config from AppData\Roaming\.config\opencode\opencode.json.
        Checks for MCP config in "mcp" section first, then falls back to root "mcpServers".
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global config
        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)
        
        # Return None if no configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_global_config(self) -> Optional[Dict]:
        """
        Extract global MCP config from AppData\Roaming\.config\opencode\opencode.json
        
        When running as administrator, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        return extract_opencode_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="OpenCode",
            parent_levels=5  # AppData\Roaming\.config\opencode\opencode.json -> 5 levels up = home
        )

