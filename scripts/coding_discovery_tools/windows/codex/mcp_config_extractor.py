"""
MCP config extraction for Codex on Windows systems.

Codex uses TOML format for configuration files located at ~/.codex/config.toml.
This extractor parses the TOML file to extract MCP server configurations.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import transform_mcp_servers_to_array
from ...windows_extraction_helpers import is_running_as_admin

logger = logging.getLogger(__name__)

# Constants
_TOOL_NAME = "Codex"
_PARENT_LEVELS = 1  # ~/.codex/config.toml -> 1 level up = ~/.codex
# Pattern matches both camelCase [mcpServers.*] and snake_case [mcp_servers.*] formats
_MCP_SERVERS_SECTION_PATTERN = re.compile(
    r'\[mcp_?[Ss]ervers\.([^\]]+)\]\s*\n(.*?)(?=\n\s*\[|\Z)',
    re.MULTILINE | re.DOTALL
)
_KEY_VALUE_PATTERN = re.compile(
    r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?=\n\s*[a-zA-Z_][a-zA-Z0-9_]*\s*=|$)',
    re.MULTILINE | re.DOTALL
)
_ARRAY_ELEMENT_PATTERN = re.compile(r'["\']([^"\']+)["\']|([^,\]]+)')
_INLINE_TABLE_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*["\']?([^"\',}]+)["\']?')
_COMMENT_MARKER = '#'
_QUOTE_CHARS = '"\''  # Single string containing both quote characters
_BOOLEAN_TRUE = 'true'
_BOOLEAN_FALSE = 'false'


def _parse_toml_value(value: str) -> Union[str, bool, List[str], Dict[str, str], None]:
    """
    Parse a TOML value string into appropriate Python type.
    
    Handles:
    - Strings (quoted and unquoted)
    - Booleans (true/false)
    - Arrays ([...])
    - Inline tables ({...})
    
    Args:
        value: Raw value string from TOML
        
    Returns:
        Parsed Python value (str, bool, list, dict, or None)
    """
    value = value.strip()
    
    # Boolean values
    if value.lower() == _BOOLEAN_TRUE:
        return True
    if value.lower() == _BOOLEAN_FALSE:
        return False
    
    # Arrays
    if value.startswith('[') and value.endswith(']'):
        return _parse_array(value)
    
    # Inline tables
    if value.startswith('{') and value.endswith('}'):
        return _parse_inline_table(value)
    
    # Strings (quoted or unquoted)
    return _parse_string(value)


def _parse_array(value: str) -> List[str]:
    """
    Parse a TOML array value.
    
    Args:
        value: Array string in format [...]
        
    Returns:
        List of parsed array elements
    """
    array_content = value[1:-1].strip()
    if not array_content:
        return []
    
    elements: List[str] = []
    for elem_match in _ARRAY_ELEMENT_PATTERN.finditer(array_content):
        elem = elem_match.group(1) or elem_match.group(2)
        if elem:
            elem = elem.strip()
            # Remove surrounding quotes if present
            if len(elem) >= 2 and elem[0] == elem[-1] and elem[0] in _QUOTE_CHARS:
                elem = elem[1:-1]
            elements.append(elem)
    return elements


def _parse_inline_table(value: str) -> Dict[str, str]:
    """
    Parse a TOML inline table value.
    
    Args:
        value: Inline table string in format {...}
        
    Returns:
        Dictionary of key-value pairs
    """
    table_content = value[1:-1].strip()
    table: Dict[str, str] = {}
    
    for match in _INLINE_TABLE_PATTERN.finditer(table_content):
        key = match.group(1).strip()
        table_value = match.group(2).strip()
        # Remove surrounding quotes if present
        if len(table_value) >= 2 and table_value[0] == table_value[-1] and table_value[0] in _QUOTE_CHARS:
            table_value = table_value[1:-1]
        table[key] = table_value
    
    return table


def _parse_string(value: str) -> str:
    """
    Parse a TOML string value (quoted or unquoted).
    
    Args:
        value: String value
        
    Returns:
        Unquoted string value
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTE_CHARS:
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    """
    Strip inline comments from a TOML value.
    
    Args:
        value: Value string that may contain comments
        
    Returns:
        Value string with comments removed
    """
    if _COMMENT_MARKER in value:
        return value.split(_COMMENT_MARKER)[0].strip()
    return value


def _parse_server_section(section_content: str) -> Dict[str, Any]:
    """
    Parse key-value pairs from a TOML server section.
    
    Args:
        section_content: Content of a server section
        
    Returns:
        Dictionary of server configuration key-value pairs
    """
    server_config: Dict[str, Any] = {}
    
    for match in _KEY_VALUE_PATTERN.finditer(section_content):
        key = match.group(1).strip()
        raw_value = match.group(2).strip()
        
        # Strip inline comments
        value = _strip_inline_comment(raw_value)
        
        # Parse value into appropriate type
        parsed_value = _parse_toml_value(value)
        if parsed_value is not None:
            server_config[key] = parsed_value
    
    return server_config


def parse_toml_mcp_servers(content: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Parse MCP servers from TOML content.
    
    Supports two section formats:
    1. camelCase: [mcpServers.server_name]
    2. snake_case: [mcp_servers.server_name]
    
    Example formats:
    [mcpServers.linear]
    command = "npx"
    args = ["-y", "mcp-remote", "https://mcp.linear.app/sse"]
    disabled = false
    
    [mcp_servers.linear]
    type = "http"
    url = "https://mcp.linear.app/mcp"
    
    Args:
        content: TOML file content as string
        
    Returns:
        Dictionary mapping server names to their configurations, or None if not found
    """
    mcp_servers: Dict[str, Dict[str, Any]] = {}
    
    for match in _MCP_SERVERS_SECTION_PATTERN.finditer(content):
        server_name = match.group(1).strip()
        # Remove surrounding quotes if present
        if len(server_name) >= 2 and server_name[0] == server_name[-1] and server_name[0] in _QUOTE_CHARS:
            server_name = server_name[1:-1]
        section_content = match.group(2)
        
        server_config = _parse_server_section(section_content)
        if server_config:
            mcp_servers[server_name] = server_config
    
    return mcp_servers if mcp_servers else None


def _calculate_config_path(config_path: Path, parent_levels: int) -> Path:
    """
    Calculate the parent path by going up the specified number of levels.
    
    Args:
        config_path: Starting path
        parent_levels: Number of parent directories to traverse
        
    Returns:
        Calculated parent path
    """
    result_path = config_path
    for _ in range(parent_levels):
        result_path = result_path.parent
    return result_path


def read_codex_toml_mcp_config(
    config_path: Path,
    tool_name: str = _TOOL_NAME,
    parent_levels: int = _PARENT_LEVELS
) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
    """
    Read and parse Codex TOML config file to extract MCP servers.
    
    This function follows the same pattern as read_global_mcp_config but for TOML format.
    
    Args:
        config_path: Path to the config.toml file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
        
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
        
        if not mcp_servers_array:
            return None
        
        global_config_path = _calculate_config_path(config_path, parent_levels)
        return {
            "path": str(global_config_path),
            "mcpServers": mcp_servers_array
        }
    except PermissionError as e:
        logger.warning(
            f"Permission denied reading global {tool_name} MCP config {config_path}: {e}"
        )
    except Exception as e:
        logger.warning(
            f"Error reading global {tool_name} MCP config {config_path}: {e}"
        )
    
    return None


def _is_admin_user() -> Tuple[bool, Optional[Path]]:
    """
    Check if running as admin user and get users directory.
    
    Returns:
        Tuple of (is_admin, users_dir) where users_dir is None if not admin
    """
    is_admin = is_running_as_admin()
    users_dir = Path("C:\\Users") if is_admin else None
    return is_admin, users_dir


def _extract_config_from_user_directories(
    global_config_path: Path,
    tool_name: str,
    parent_levels: int
) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
    """
    Extract MCP config from all user directories (when running as administrator).
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
        
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    is_admin, users_dir = _is_admin_user()
    
    if not is_admin or not users_dir or not users_dir.exists():
        return None
    
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith('.'):
            continue
        
        try:
            user_config_path = user_dir / global_config_path.relative_to(Path.home())
            if user_config_path.exists():
                config = read_codex_toml_mcp_config(user_config_path, tool_name, parent_levels)
                if config:
                    return config
        except (ValueError, OSError):
            continue
    
    return None


def extract_codex_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = _TOOL_NAME,
    parent_levels: int = _PARENT_LEVELS
) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
    """
    Extract global Codex MCP config with support for admin user.
    
    Reuses the pattern from extract_global_mcp_config_with_root_support
    but calls read_codex_toml_mcp_config for TOML parsing.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
        
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    # When running as administrator, check all user directories first
    config = _extract_config_from_user_directories(
        global_config_path, tool_name, parent_levels
    )
    if config:
        return config
    
    # Fallback to current user's config
    if global_config_path.exists():
        return read_codex_toml_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


class WindowsCodexMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Codex MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".codex" / "config.toml"

    def extract_mcp_config(self) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """
        Extract Codex MCP configuration on Windows.
        
        Extracts global MCP config from ~/.codex/config.toml.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        global_config = self._extract_global_config()
        if not global_config:
            return None
        
        return {
            "projects": [global_config]
        }

    def _extract_global_config(self) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
        """
        Extract global MCP config from ~/.codex/config.toml.
        
        When running as administrator, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        
        Returns:
            Dict with 'path' and 'mcpServers' keys, or None if not found
        """
        return extract_codex_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name=_TOOL_NAME,
            parent_levels=_PARENT_LEVELS
        )

