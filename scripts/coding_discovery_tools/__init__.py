"""
AI Tools Discovery Package

Detects AI coding tools (Cursor, Claude Code) on macOS and Windows systems.

This package provides a modular, class-based architecture for detecting AI tools:
- Abstract base classes for extensibility
- OS-specific implementations (macOS, Windows)
- Factory pattern for creating appropriate detectors
- Backward-compatible function interfaces
"""

from .ai_tools_discovery import AIToolsDetector, main
from .coding_tool_base import BaseDeviceIdExtractor, BaseToolDetector, BaseCursorRulesExtractor, BaseClaudeRulesExtractor
from .coding_tool_factory import DeviceIdExtractorFactory, ToolDetectorFactory, CursorRulesExtractorFactory, ClaudeRulesExtractorFactory

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

