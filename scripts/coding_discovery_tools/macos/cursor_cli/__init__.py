"""
Cursor CLI detection and extraction for macOS

Supports:
1. Tool presence detection (cursor command availability)
2. Rules extraction from ~/.cursor/rules/*.mdc and .cursor/rules/*.mdc
3. MCP server detection from ~/.cursor/mcp.json and .cursor/mcp.json
4. Permission detection from ~/.cursor/cli-config.json and .cursor/cli.json
"""

from .cursor_cli import MacOSCursorCliDetector
from .cursor_cli_rules_extractor import MacOSCursorCliRulesExtractor
from .settings_extractor import MacOSCursorCliSettingsExtractor
from .mcp_config_extractor import MacOSCursorCliMCPConfigExtractor

__all__ = [
    'MacOSCursorCliDetector',
    'MacOSCursorCliRulesExtractor',
    'MacOSCursorCliSettingsExtractor',
    'MacOSCursorCliMCPConfigExtractor',
]
