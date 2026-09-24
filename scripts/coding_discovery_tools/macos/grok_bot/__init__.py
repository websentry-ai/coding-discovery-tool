"""
Grok Bot detection and extraction for macOS
"""

from .grok_bot import MacOSGrokBotDetector
from .grok_bot_rules_extractor import MacOSGrokBotRulesExtractor

__all__ = [
    'MacOSGrokBotDetector',
    'MacOSGrokBotRulesExtractor',
]
