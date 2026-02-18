"""
Cursor CLI detection and extraction for macOS
"""

from .cursor_cli import MacOSCursorCliDetector
from .settings_extractor import MacOSCursorCliSettingsExtractor

__all__ = [
    'MacOSCursorCliDetector',
    'MacOSCursorCliSettingsExtractor',
]
