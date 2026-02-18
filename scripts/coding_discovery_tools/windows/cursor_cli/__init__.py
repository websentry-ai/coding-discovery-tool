"""
Cursor CLI detection and extraction for Windows
"""

from .cursor_cli import WindowsCursorCliDetector
from .settings_extractor import WindowsCursorCliSettingsExtractor

__all__ = [
    'WindowsCursorCliDetector',
    'WindowsCursorCliSettingsExtractor',
]
