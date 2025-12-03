"""
MCP config extraction for Codex on macOS systems.

Codex uses TOML format for configuration files located at ~/.codex/config.toml.
This extractor parses the TOML file to extract MCP server configurations.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import transform_mcp_servers_to_array

logger = logging.getLogger(__name__)


def parse_toml_mcp_servers(content: str) -> Optional[Dict]:
    """
    Parse MCP servers from TOML content.
    
    Extracts servers from sections like [mcp_servers.server_name] with format:
    [mcp_servers.context7]
    type = "http"
    url = "https://mcp.context7.com/mcp"
    
    Args:
        content: TOML file content as string
        
    Returns:
        Dictionary of MCP servers or None if not found
    """
    mcp_servers = {}
    
    # Find all [mcp_servers.*] sections
    # Pattern matches: [mcp_servers.server_name] or [mcp_servers."server name"]
    section_pattern = r'\[mcp_servers\.([^\]]+)\]\s*\n(.*?)(?=\n\s*\[|\Z)'
    
    for match in re.finditer(section_pattern, content, re.MULTILINE | re.DOTALL):
        server_name = match.group(1).strip().strip('"\'')
        section_content = match.group(2)
        
        # Parse key-value pairs in the section
        # Format: key = "value" or key = value (handles quoted and unquoted strings)
        server_config = {}
        kv_pattern = r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?=\n|$)'
        
        for kv_match in re.finditer(kv_pattern, section_content, re.MULTILINE):
            key = kv_match.group(1).strip()
            value = kv_match.group(2).strip()
            # Strip inline comments (everything after #)
            if '#' in value:
                value = value.split('#')[0].strip()
            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            server_config[key] = value
        
        if server_config:
            mcp_servers[server_name] = server_config
    
    return mcp_servers if mcp_servers else None


def read_codex_toml_mcp_config(
    config_path: Path,
    tool_name: str = "Codex",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Read and parse Codex TOML config file to extract MCP servers.
    
    This function follows the same pattern as read_global_mcp_config but for TOML format.
    
    Args:
        config_path: Path to the config.toml file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
                      For ~/.codex/config.toml -> 2 levels up = ~
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        mcp_servers_obj = parse_toml_mcp_servers(content)
        
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
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_codex_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = "Codex",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Extract global Codex MCP config with support for root/admin user.
    
    Reuses the pattern from extract_global_mcp_config_with_root_support
    but calls read_codex_toml_mcp_config for TOML parsing.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    # Reuse the existing helper's root support logic pattern
    # We adapt it to use our TOML reader instead of JSON reader
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
                        config = read_codex_toml_mcp_config(user_config_path, tool_name, parent_levels)
                        if config:
                            return config
                except (ValueError, OSError):
                    continue
        
        # Fallback to admin's own global config
        if global_config_path.exists():
            return read_codex_toml_mcp_config(global_config_path, tool_name, parent_levels)
    else:
        # For regular users, check their own home directory
        if global_config_path.exists():
            return read_codex_toml_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


class MacOSCodexMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Codex MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".codex" / "config.toml"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Codex MCP configuration on macOS.
        
        Extracts global MCP config from ~/.codex/config.toml.
        
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
        Extract global MCP config from ~/.codex/config.toml
        
        When running as root, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        return extract_codex_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Codex",
            parent_levels=2  # ~/.codex/config.toml -> 2 levels up = ~
        )

