"""
GitHub Copilot CLI detection and extraction for Windows.

The GitHub Copilot CLI (`@github/copilot`) is the standalone agentic terminal
tool. It is a distinct product from the GitHub Copilot VS Code extension and
JetBrains plugin, with its own home directory at ``%USERPROFILE%\\.copilot``
(i.e. ``~/.copilot``) — general config plus ``~/.copilot/mcp-config.json`` for
MCP servers. The detector and MCP extractor reuse the OS-agnostic macOS logic;
only the all-users (``C:\\Users``) scan is Windows-specific.
"""

from .copilot_cli import WindowsCopilotCliDetector
from .mcp_config_extractor import WindowsCopilotCliMCPConfigExtractor

__all__ = [
    'WindowsCopilotCliDetector',
    'WindowsCopilotCliMCPConfigExtractor',
]
