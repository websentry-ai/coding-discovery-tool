"""
AI Tools Discovery Package

Detects AI coding tools (Cursor, Claude Code) on macOS and Windows systems.

This package provides a modular, class-based architecture for detecting AI tools:
- Abstract base classes for extensibility
- OS-specific implementations (macOS, Windows)
- Factory pattern for creating appropriate detectors
- Backward-compatible function interfaces
"""

from .coding_tool_base import BaseDeviceIdExtractor, BaseToolDetector, BaseCursorRulesExtractor, BaseClaudeRulesExtractor
from .coding_tool_factory import DeviceIdExtractorFactory, ToolDetectorFactory, CursorRulesExtractorFactory, ClaudeRulesExtractorFactory

def __getattr__(name):
    if name in ('AIToolsDetector', 'main'):
        from .ai_tools_discovery import AIToolsDetector, main
        globals()[name] = locals()[name]
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Main detector
    'AIToolsDetector',
    'main',
    # Base classes
    'BaseDeviceIdExtractor',
    'BaseToolDetector',
    'BaseCursorRulesExtractor',
    'BaseClaudeRulesExtractor',
    # Factories
    'DeviceIdExtractorFactory',
    'ToolDetectorFactory',
    'CursorRulesExtractorFactory',
    'ClaudeRulesExtractorFactory',
]

