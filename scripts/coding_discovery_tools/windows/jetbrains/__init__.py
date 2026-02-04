"""JetBrains IDE detection and configuration extraction for Windows."""

from .jetbrains import WindowsJetBrainsDetector
from .mcp_config_extractor import WindowsJetBrainsMCPConfigExtractor

__all__ = ['WindowsJetBrainsDetector', 'WindowsJetBrainsMCPConfigExtractor']
