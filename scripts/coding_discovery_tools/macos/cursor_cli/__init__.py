"""
Cursor CLI detection and extraction for macOS

Supports:
1. Tool presence detection (cursor command availability)
2. MCP server detection from ~/.cursor/mcp.json and .cursor/mcp.json
3. Permission detection from ~/.cursor/cli-config.json and .cursor/cli.json
"""

from .cursor_cli import MacOSCursorCliDetector
from .settings_extractor import MacOSCursorCliSettingsExtractor
from .mcp_config_extractor import MacOSCursorCliMCPConfigExtractor

__all__ = [
    'MacOSCursorCliDetector',
    'MacOSCursorCliSettingsExtractor',
    'MacOSCursorCliMCPConfigExtractor',
]
