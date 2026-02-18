"""
Windows-specific implementations for AI tools discovery
"""

from .device_id import WindowsDeviceIdExtractor
from .cursor import WindowsCursorDetector
from .cursor_cli import WindowsCursorCliDetector
from .claude_code import WindowsClaudeDetector
from .windsurf import WindowsWindsurfDetector
from .cline import WindowsClineDetector
from .kilocode import WindowsKiloCodeDetector
from .roo_code import WindowsRooDetector
from .opencode import WindowsOpenCodeDetector
from .openclaw import WindowsOpenClawDetector
from .replit import WindowsReplitDetector
from .codex import WindowsCodexDetector
from .jetbrains import WindowsJetBrainsDetector
from .github_copilot import (
    WindowsGitHubCopilotDetector,
    WindowsGitHubCopilotMCPConfigExtractor,
    WindowsGitHubCopilotRulesExtractor,
)

__all__ = [
    'WindowsDeviceIdExtractor',
    'WindowsCursorDetector',
    'WindowsCursorCliDetector',
    'WindowsClaudeDetector',
    'WindowsWindsurfDetector',
    'WindowsClineDetector',
    'WindowsKiloCodeDetector',
    'WindowsRooDetector',
    'WindowsOpenCodeDetector',
    'WindowsOpenClawDetector',
    'WindowsReplitDetector',
    'WindowsCodexDetector',
    'WindowsJetBrainsDetector',
    'WindowsGitHubCopilotDetector',
    'WindowsGitHubCopilotMCPConfigExtractor',
    'WindowsGitHubCopilotRulesExtractor',
]

