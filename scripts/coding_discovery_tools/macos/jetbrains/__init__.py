"""JetBrains IDE detection and configuration extraction for macOS."""

from .jetbrains import MacOSJetBrainsDetector
from .mcp_config_extractor import MacOSJetBrainsMCPConfigExtractor

__all__ = ['MacOSJetBrainsDetector', 'MacOSJetBrainsMCPConfigExtractor']
