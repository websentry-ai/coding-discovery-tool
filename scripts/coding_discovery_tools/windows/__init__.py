"""
Windows-specific implementations for AI tools discovery
"""

from .device_id import WindowsDeviceIdExtractor
from .cursor import WindowsCursorDetector
from .claude_code import WindowsClaudeDetector
from .windsurf import WindowsWindsurfDetector
from .kilocode import WindowsKiloCodeDetector

__all__ = [
    'WindowsDeviceIdExtractor',
    'WindowsCursorDetector',
    'WindowsClaudeDetector',
    'WindowsWindsurfDetector',
    'WindowsKiloCodeDetector',
]

