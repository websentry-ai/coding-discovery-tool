"""
macOS-specific implementations for AI tools discovery
"""

from .device_id import MacOSDeviceIdExtractor
from .cursor import MacOSCursorDetector
from .cursor_cli import MacOSCursorCliDetector
from .claude_code import MacOSClaudeDetector
from .claude_cowork import MacOSClaudeCoworkDetector, MacOSClaudeCoworkSkillsExtractor
from .claude_desktop import MacOSClaudeDesktopDetector, MacOSClaudeDesktopMCPConfigExtractor
from .windsurf import MacOSWindsurfDetector
from .roo_code import MacOSRooDetector
from .cline import MacOSClineDetector
from .gemini_cli import MacOSGeminiCliDetector
from .codex import MacOSCodexDetector
from .replit import MacOSReplitDetector
from .opencode import MacOSOpenCodeDetector
from .pi import MacOSPiDetector
from .junie import MacOSJunieDetector
from .github_copilot_xcode import MacOSGitHubCopilotXcodeDetector
from .xcode import MacOSXcodeDetector

__all__ = [
    'MacOSDeviceIdExtractor',
    'MacOSCursorDetector',
    'MacOSCursorCliDetector',
    'MacOSClaudeDetector',
    'MacOSClaudeCoworkDetector',
    'MacOSClaudeCoworkSkillsExtractor',
    'MacOSClaudeDesktopDetector',
    'MacOSClaudeDesktopMCPConfigExtractor',
    'MacOSWindsurfDetector',
    'MacOSRooDetector',
    'MacOSClineDetector',
    'MacOSGeminiCliDetector',
    'MacOSCodexDetector',
    'MacOSReplitDetector',
    'MacOSOpenCodeDetector',
    'MacOSPiDetector',
    'MacOSJunieDetector',
    'MacOSGitHubCopilotXcodeDetector',
    'MacOSXcodeDetector',
]

