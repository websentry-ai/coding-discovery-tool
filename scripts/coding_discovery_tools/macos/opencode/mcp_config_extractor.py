"""
MCP config extraction for OpenCode on macOS systems.
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
    parent_levels: int = 3
) -> Optional[Dict]:
    """
    Read and parse OpenCode JSON config file to extract MCP servers.
    
    Checks for MCP config in "mcp" section first, then falls back to root "mcpServers".
    
    Args:
        config_path: Path to the opencode.json file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
                      For ~/.config/opencode/opencode.json -> 3 levels up = ~
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        # Check for MCP config in "mcp" section first (as per user spec)
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
    parent_levels: int = 3
) -> Optional[Dict]:
    """
    Extract global OpenCode MCP config with support for root/admin user.
    
    Reuses the pattern from extract_global_mcp_config_with_root_support
    but calls read_opencode_mcp_config for JSON parsing with "mcp" section support.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    import platform
    
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from ...macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    
    # When running as admin/root, check all user directories
    if is_admin and users_dir and users_dir.exists():
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
        
        # Fallback to admin's own global config
        if global_config_path.exists():
            return read_opencode_mcp_config(global_config_path, tool_name, parent_levels)
    else:
        # For regular users, check their own home directory
        if global_config_path.exists():
            return read_opencode_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


class MacOSOpenCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for OpenCode MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".config" / "opencode" / "opencode.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract OpenCode MCP configuration on macOS.
        
        Extracts global MCP config from ~/.config/opencode/opencode.json.
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
        Extract global MCP config from ~/.config/opencode/opencode.json
        
        When running as root, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        return extract_opencode_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="OpenCode",
            parent_levels=3  # ~/.config/opencode/opencode.json -> 3 levels up = ~
        )

