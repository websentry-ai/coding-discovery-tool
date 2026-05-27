"""
Shared TOML MCP config parsing helpers.

Used by both macOS and Windows Codex extractors to avoid duplication.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .mcp_extraction_helpers import transform_mcp_servers_to_array

logger = logging.getLogger(__name__)

# Tool name shared across Codex extractors
_TOOL_NAME = "Codex"

# Number of parent levels to traverse for global config:
# ~/.codex/config.toml -> 1 level up = ~/.codex
_PARENT_LEVELS = 1

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
_QUOTE_CHARS = '"\''
_BOOLEAN_TRUE = 'true'
_BOOLEAN_FALSE = 'false'


def _parse_toml_value(value: str) -> Union[str, bool, List[str], Dict[str, str], None]:
    """Parse a TOML value string into the appropriate Python type."""
    value = value.strip()

    if value.lower() == _BOOLEAN_TRUE:
        return True
    if value.lower() == _BOOLEAN_FALSE:
        return False

    if value.startswith('[') and value.endswith(']'):
        return _parse_array(value)

    if value.startswith('{') and value.endswith('}'):
        return _parse_inline_table(value)

    return _parse_string(value)


def _parse_array(value: str) -> List[str]:
    """Parse a TOML array value."""
    array_content = value[1:-1].strip()
    if not array_content:
        return []

    elements: List[str] = []
    for elem_match in _ARRAY_ELEMENT_PATTERN.finditer(array_content):
        elem = elem_match.group(1) or elem_match.group(2)
        if elem:
            elem = elem.strip()
            if len(elem) >= 2 and elem[0] == elem[-1] and elem[0] in _QUOTE_CHARS:
                elem = elem[1:-1]
            elements.append(elem)
    return elements


def _parse_inline_table(value: str) -> Dict[str, str]:
    """Parse a TOML inline table value."""
    table_content = value[1:-1].strip()
    table: Dict[str, str] = {}

    for match in _INLINE_TABLE_PATTERN.finditer(table_content):
        key = match.group(1).strip()
        table_value = match.group(2).strip()
        if len(table_value) >= 2 and table_value[0] == table_value[-1] and table_value[0] in _QUOTE_CHARS:
            table_value = table_value[1:-1]
        table[key] = table_value

    return table


def _parse_string(value: str) -> str:
    """Parse a TOML string value (quoted or unquoted)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTE_CHARS:
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    """Strip inline comments from a TOML value."""
    if _COMMENT_MARKER in value:
        return value.split(_COMMENT_MARKER)[0].strip()
    return value


def _parse_server_section(section_content: str) -> Dict[str, Any]:
    """Parse key-value pairs from a TOML server section."""
    server_config: Dict[str, Any] = {}

    for match in _KEY_VALUE_PATTERN.finditer(section_content):
        key = match.group(1).strip()
        raw_value = match.group(2).strip()
        value = _strip_inline_comment(raw_value)
        parsed_value = _parse_toml_value(value)
        if parsed_value is not None:
            server_config[key] = parsed_value

    return server_config


def parse_toml_mcp_servers(content: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Parse MCP servers from TOML content. Returns a dict keyed by server name.

    Supports both [mcpServers.<name>] and [mcp_servers.<name>] section styles.
    Nested sub-tables (e.g. [mcp_servers.foo.http_headers]) are dropped so they
    don't surface as spurious top-level entries or leak secrets outward.
    """
    try:
        import tomllib
    except ImportError:
        return _parse_toml_mcp_servers_regex(content)

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None

    servers = data['mcp_servers'] if 'mcp_servers' in data else data.get('mcpServers')
    if not isinstance(servers, dict):
        return None

    result: Dict[str, Dict[str, Any]] = {}
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        flat = {
            k: v for k, v in config.items()
            if not isinstance(v, dict)
            and not (isinstance(v, list) and any(isinstance(e, dict) for e in v))
        }
        if flat:
            result[name] = flat
    return result if result else None


def _parse_toml_mcp_servers_regex(content: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Fallback parser for Python <3.11. Skips nested sub-tables and dict/list-of-dict fields."""
    mcp_servers: Dict[str, Dict[str, Any]] = {}

    for match in _MCP_SERVERS_SECTION_PATTERN.finditer(content):
        raw_name = match.group(1).strip()
        is_quoted = (
            len(raw_name) >= 2
            and raw_name[0] == raw_name[-1]
            and raw_name[0] in _QUOTE_CHARS
        )
        server_name = raw_name[1:-1] if is_quoted else raw_name
        if not is_quoted and '.' in server_name:
            continue

        server_config = _parse_server_section(match.group(2))
        if not server_config:
            continue
        flat = {
            k: v for k, v in server_config.items()
            if not isinstance(v, dict)
            and not (isinstance(v, list) and any(isinstance(e, dict) for e in v))
        }
        if flat:
            mcp_servers[server_name] = flat

    return mcp_servers if mcp_servers else None


def _calculate_config_path(config_path: Path, parent_levels: int) -> Path:
    """Calculate the parent path by traversing up the specified number of levels."""
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
    Read and parse a Codex TOML config file to extract MCP servers.

    Args:
        config_path: Path to the config.toml file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to traverse for the path key

    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        mcp_servers_obj = parse_toml_mcp_servers(content)

        if not mcp_servers_obj:
            return None

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
