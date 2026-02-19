"""
Cursor CLI detection and extraction for Windows

Supports:
1. Tool presence detection (cursor command availability)
2. Rules extraction from %USERPROFILE%\\.cursor\\rules\\*.mdc and .cursor\\rules\\*.mdc
3. MCP server detection from ~/.cursor/mcp.json and .cursor/mcp.json
4. Permission detection from ~/.cursor/cli-config.json and .cursor/cli.json
"""

from .cursor_cli import WindowsCursorCliDetector
from .cursor_cli_rules_extractor import WindowsCursorCliRulesExtractor
from .settings_extractor import WindowsCursorCliSettingsExtractor
from .mcp_config_extractor import WindowsCursorCliMCPConfigExtractor

__all__ = [
    'WindowsCursorCliDetector',
    'WindowsCursorCliRulesExtractor',
    'WindowsCursorCliSettingsExtractor',
    'WindowsCursorCliMCPConfigExtractor',
]
