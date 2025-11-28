"""
macOS-specific implementations for AI tools discovery
"""

from .device_id import MacOSDeviceIdExtractor
from .cursor import MacOSCursorDetector
from .claude_code import MacOSClaudeDetector
from .windsurf import MacOSWindsurfDetector
from .roo_code import MacOSRooDetector
from .cline import MacOSClineDetector

__all__ = [
    'MacOSDeviceIdExtractor',
    'MacOSCursorDetector',
    'MacOSClaudeDetector',
    'MacOSWindsurfDetector',
    'MacOSRooDetector',
    'MacOSClineDetector',
]

