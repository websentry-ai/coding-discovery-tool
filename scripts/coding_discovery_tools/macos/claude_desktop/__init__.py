"""
Claude Desktop detection and extraction for macOS
"""

from .claude_desktop import MacOSClaudeDesktopDetector
from .mcp_config_extractor import MacOSClaudeDesktopMCPConfigExtractor

__all__ = [
    "MacOSClaudeDesktopDetector",
    "MacOSClaudeDesktopMCPConfigExtractor",
]
