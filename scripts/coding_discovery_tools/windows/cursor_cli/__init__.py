"""
Cursor CLI detection and extraction for Windows

Supports:
1. Tool presence detection (cursor command availability)
2. MCP server detection from ~/.cursor/mcp.json and .cursor/mcp.json
3. Permission detection from ~/.cursor/cli-config.json and .cursor/cli.json
"""

from .cursor_cli import WindowsCursorCliDetector
from .settings_extractor import WindowsCursorCliSettingsExtractor
from .mcp_config_extractor import WindowsCursorCliMCPConfigExtractor

__all__ = [
    'WindowsCursorCliDetector',
    'WindowsCursorCliSettingsExtractor',
    'WindowsCursorCliMCPConfigExtractor',
]
