"""GitHub Copilot detection, rules extraction, and MCP config extraction for macOS."""

from .detect_copilot import MacOSCopilotDetector
from .mcp_config_extractor import MacOSGitHubCopilotMCPConfigExtractor
from .copilot_rules_extractor import MacOSGitHubCopilotRulesExtractor

__all__ = [
    'MacOSCopilotDetector',
    'MacOSGitHubCopilotMCPConfigExtractor',
    'MacOSGitHubCopilotRulesExtractor',
]
