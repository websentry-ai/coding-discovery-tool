"""
GitHub Copilot CLI detection and extraction for macOS.

The GitHub Copilot CLI (`@github/copilot`) is the standalone agentic terminal
tool. It is a distinct product from the GitHub Copilot VS Code extension and
JetBrains plugin, with its own home directory at ``~/.copilot/`` (general
config plus ``~/.copilot/mcp-config.json`` for MCP servers).
"""

from .copilot_cli import MacOSCopilotCliDetector
from .mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor
from .copilot_cli_rules_extractor import MacOSCopilotCliRulesExtractor
from .copilot_cli_settings_extractor import MacOSCopilotCliSettingsExtractor
from .copilot_cli_skills_extractor import MacOSCopilotCliSkillsExtractor

__all__ = [
    'MacOSCopilotCliDetector',
    'MacOSCopilotCliMCPConfigExtractor',
    'MacOSCopilotCliRulesExtractor',
    'MacOSCopilotCliSettingsExtractor',
    'MacOSCopilotCliSkillsExtractor',
]
