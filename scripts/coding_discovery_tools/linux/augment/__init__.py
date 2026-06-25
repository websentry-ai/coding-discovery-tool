"""
Augment Code detection and extraction for Linux.

Augment Code (``~/.augment/``) ships three surfaces — the Auggie CLI, the VS Code
extension, and the JetBrains plugin — that share one config dir. The detector and
extractors reuse the OS-agnostic macOS logic, overriding only the all-users scan
and the Linux filesystem primitives.
"""

from .augment import LinuxAugmentDetector
from .augment_mcp_config_extractor import LinuxAugmentMCPConfigExtractor
from .augment_rules_extractor import LinuxAugmentRulesExtractor
from .augment_settings_extractor import LinuxAugmentSettingsExtractor
from .augment_skills_extractor import LinuxAugmentSkillsExtractor

__all__ = [
    "LinuxAugmentDetector",
    "LinuxAugmentMCPConfigExtractor",
    "LinuxAugmentRulesExtractor",
    "LinuxAugmentSettingsExtractor",
    "LinuxAugmentSkillsExtractor",
]
