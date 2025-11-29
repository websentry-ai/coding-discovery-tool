"""
Cline detection and extraction for macOS
"""

from .cline import MacOSClineDetector
from .cline_rules_extractor import MacOSClineRulesExtractor
from .mcp_config_extractor import MacOSClineMCPConfigExtractor

__all__ = ['MacOSClineDetector', 'MacOSClineRulesExtractor', 'MacOSClineMCPConfigExtractor']

