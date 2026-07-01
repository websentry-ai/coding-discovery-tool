"""
Augment Code detection and extraction for Windows.

Augment Code (``%USERPROFILE%\\.augment``) ships three surfaces — the Auggie CLI,
the VS Code extension, and the JetBrains plugin — that share one config dir. The
detector and extractors reuse the OS-agnostic macOS logic; only the all-users
(``C:\\Users``) scan and the Windows filesystem primitives are overridden.
"""

from .augment import WindowsAugmentDetector
from .augment_mcp_config_extractor import WindowsAugmentMCPConfigExtractor
from .augment_rules_extractor import WindowsAugmentRulesExtractor
from .augment_settings_extractor import WindowsAugmentSettingsExtractor
from .augment_skills_extractor import WindowsAugmentSkillsExtractor

__all__ = [
    'WindowsAugmentDetector',
    'WindowsAugmentMCPConfigExtractor',
    'WindowsAugmentRulesExtractor',
    'WindowsAugmentSettingsExtractor',
    'WindowsAugmentSkillsExtractor',
]
