"""
OpenCode detection and extraction for Windows
"""

from .opencode import WindowsOpenCodeDetector
from .opencode_rules_extractor import WindowsOpenCodeRulesExtractor
from .mcp_config_extractor import WindowsOpenCodeMCPConfigExtractor

__all__ = [
    'WindowsOpenCodeDetector',
    'WindowsOpenCodeRulesExtractor',
    'WindowsOpenCodeMCPConfigExtractor',
]

