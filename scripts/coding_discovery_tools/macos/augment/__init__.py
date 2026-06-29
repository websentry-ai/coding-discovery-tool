"""
Augment Code detection and extraction for macOS.

Augment Code (``~/.augment/``) ships three surfaces — the Auggie CLI, the VS Code
extension, and the JetBrains plugin — that share one config dir. The detector
emits a row per surface; the shared config (MCP / rules / skills / permissions) is
attached to a single canonical surface downstream.
"""

from .augment import MacOSAugmentDetector
from .augment_mcp_config_extractor import MacOSAugmentMCPConfigExtractor
from .augment_rules_extractor import MacOSAugmentRulesExtractor
from .augment_settings_extractor import MacOSAugmentSettingsExtractor
from .augment_skills_extractor import MacOSAugmentSkillsExtractor

__all__ = [
    'MacOSAugmentDetector',
    'MacOSAugmentMCPConfigExtractor',
    'MacOSAugmentRulesExtractor',
    'MacOSAugmentSettingsExtractor',
    'MacOSAugmentSkillsExtractor',
]
