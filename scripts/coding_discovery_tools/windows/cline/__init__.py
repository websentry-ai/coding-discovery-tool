"""
Cline detection and extraction for Windows
"""

from .cline import WindowsClineDetector
from .cline_rules_extractor import WindowsClineRulesExtractor
from .mcp_config_extractor import WindowsClineMCPConfigExtractor
from .skills_extractor import WindowsClineSkillsExtractor

__all__ = ['WindowsClineDetector', 'WindowsClineRulesExtractor', 'WindowsClineMCPConfigExtractor', 'WindowsClineSkillsExtractor']

