"""
macOS-specific implementations for AI tools discovery
"""

from .device_id import MacOSDeviceIdExtractor
from .cursor import MacOSCursorDetector
from .claude_code import MacOSClaudeDetector
from .windsurf import MacOSWindsurfDetector
from .roo_code import MacOSRooDetector
from .cline import MacOSClineDetector
from .gemini_cli import MacOSGeminiCliDetector
from .codex import MacOSCodexDetector
from .replit import MacOSReplitDetector
from .opencode import MacOSOpenCodeDetector

__all__ = [
    'MacOSDeviceIdExtractor',
    'MacOSCursorDetector',
    'MacOSClaudeDetector',
    'MacOSWindsurfDetector',
    'MacOSRooDetector',
    'MacOSClineDetector',
    'MacOSGeminiCliDetector',
    'MacOSCodexDetector',
    'MacOSReplitDetector',
    'MacOSOpenCodeDetector',
]

