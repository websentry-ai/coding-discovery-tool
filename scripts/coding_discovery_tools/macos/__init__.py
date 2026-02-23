"""
macOS-specific implementations for AI tools discovery
"""

from .device_id import MacOSDeviceIdExtractor
from .cursor import MacOSCursorDetector
from .cursor_cli import MacOSCursorCliDetector
from .claude_code import MacOSClaudeDetector
from .windsurf import MacOSWindsurfDetector
from .roo_code import MacOSRooDetector
from .cline import MacOSClineDetector
from .gemini_cli import MacOSGeminiCliDetector
from .codex import MacOSCodexDetector
from .replit import MacOSReplitDetector
from .opencode import MacOSOpenCodeDetector
from .junie import MacOSJunieDetector

__all__ = [
    'MacOSDeviceIdExtractor',
    'MacOSCursorDetector',
    'MacOSCursorCliDetector',
    'MacOSClaudeDetector',
    'MacOSWindsurfDetector',
    'MacOSRooDetector',
    'MacOSClineDetector',
    'MacOSGeminiCliDetector',
    'MacOSCodexDetector',
    'MacOSReplitDetector',
    'MacOSOpenCodeDetector',
    'MacOSJunieDetector',
]

