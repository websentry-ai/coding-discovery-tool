"""
Windows-specific implementations for AI tools discovery
"""

from .device_id import WindowsDeviceIdExtractor
from .cursor import WindowsCursorDetector
from .claude_code import WindowsClaudeDetector
from .windsurf import WindowsWindsurfDetector
from .cline import WindowsClineDetector
from .kilocode import WindowsKiloCodeDetector
from .roo_code import WindowsRooDetector
from .codex import WindowsCodexDetector

__all__ = [
    'WindowsDeviceIdExtractor',
    'WindowsCursorDetector',
    'WindowsClaudeDetector',
    'WindowsWindsurfDetector',
    'WindowsClineDetector',
    'WindowsKiloCodeDetector',
    'WindowsRooDetector',
    'WindowsCodexDetector',
]

