"""
GitHub Copilot CLI detection and extraction for Linux.

The standalone ``@github/copilot`` CLI (distinct from the VS Code extension /
JetBrains plugin) keeps its config under ``~/.copilot/``. The detector and
extractors reuse the OS-agnostic macOS logic, overriding only the all-users scan
and the Linux filesystem primitives.
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
