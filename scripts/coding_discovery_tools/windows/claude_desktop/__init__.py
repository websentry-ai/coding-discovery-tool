"""
Claude Desktop detection and extraction for Windows
"""

from .claude_desktop import WindowsClaudeDesktopDetector
from .mcp_config_extractor import WindowsClaudeDesktopMCPConfigExtractor

__all__ = [
    "WindowsClaudeDesktopDetector",
    "WindowsClaudeDesktopMCPConfigExtractor",
]
