"""
GitHub Copilot CLI detection and extraction for Linux.

The GitHub Copilot CLI (`@github/copilot`) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin, with
its own home directory at ``~/.copilot/`` (general config plus
``~/.copilot/mcp-config.json`` for MCP servers). The detector and extractors reuse
the OS-agnostic macOS logic; only the all-users scan and the Linux-specific
filesystem primitives are overridden.
"""

from .copilot_cli import LinuxCopilotCliDetector
from .mcp_config_extractor import LinuxCopilotCliMCPConfigExtractor
from .copilot_cli_rules_extractor import LinuxCopilotCliRulesExtractor
from .copilot_cli_settings_extractor import LinuxCopilotCliSettingsExtractor
from .copilot_cli_skills_extractor import LinuxCopilotCliSkillsExtractor

__all__ = [
    "LinuxCopilotCliDetector",
    "LinuxCopilotCliMCPConfigExtractor",
    "LinuxCopilotCliRulesExtractor",
    "LinuxCopilotCliSettingsExtractor",
    "LinuxCopilotCliSkillsExtractor",
]
