"""
Factory classes for creating OS-specific detectors.

This module provides factory classes that create appropriate detector instances
based on the operating system.
"""

import platform
from typing import Optional

from .coding_tool_base import BaseDeviceIdExtractor, BaseToolDetector, BaseCursorRulesExtractor, BaseClaudeRulesExtractor, BaseMCPConfigExtractor
from .macos import MacOSDeviceIdExtractor, MacOSCursorDetector, MacOSClaudeDetector
from .macos.cursor.cursor_rules_extractor import MacOSCursorRulesExtractor
from .macos.cursor.mcp_config_extractor import MacOSCursorMCPConfigExtractor
from .macos.claude_code.claude_rules_extractor import MacOSClaudeRulesExtractor
from .macos.claude_code.mcp_config_extractor import MacOSClaudeMCPConfigExtractor
from .windows import WindowsDeviceIdExtractor, WindowsCursorDetector, WindowsClaudeDetector
from .windows.cursor.cursor_rules_extractor import WindowsCursorRulesExtractor
from .windows.cursor.mcp_config_extractor import WindowsCursorMCPConfigExtractor
from .windows.claude_code.claude_rules_extractor import WindowsClaudeRulesExtractor
from .windows.claude_code.mcp_config_extractor import WindowsClaudeMCPConfigExtractor
from .linux import LinuxDeviceIdExtractor, LinuxCursorDetector, LinuxClaudeDetector
from .linux.cursor.cursor_rules_extractor import LinuxCursorRulesExtractor
from .linux.cursor.mcp_config_extractor import LinuxCursorMCPConfigExtractor
from .linux.claude_code.claude_rules_extractor import LinuxClaudeRulesExtractor
from .linux.claude_code.mcp_config_extractor import LinuxClaudeMCPConfigExtractor


class DeviceIdExtractorFactory:
    """Factory for creating OS-specific device ID extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseDeviceIdExtractor:
        """
        Create appropriate device ID extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseDeviceIdExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSDeviceIdExtractor()
        elif os_name == "Windows":
            return WindowsDeviceIdExtractor()
        elif os_name == "Linux":
            return LinuxDeviceIdExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class ToolDetectorFactory:
    """Factory for creating OS-specific tool detectors."""

    @staticmethod
    def create_cursor_detector(os_name: Optional[str] = None) -> BaseToolDetector:
        """
        Create appropriate Cursor detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCursorDetector()
        elif os_name == "Windows":
            return WindowsCursorDetector()
        elif os_name == "Linux":
            return LinuxCursorDetector()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

    @staticmethod
    def create_claude_detector(os_name: Optional[str] = None) -> BaseToolDetector:
        """
        Create appropriate Claude Code detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClaudeDetector()
        elif os_name == "Windows":
            return WindowsClaudeDetector()
        elif os_name == "Linux":
            return LinuxClaudeDetector()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

    @staticmethod
    def create_all_tool_detectors(os_name: Optional[str] = None) -> list:
        """
        Create all supported tool detectors for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            List of BaseToolDetector instances
        """
        if os_name is None:
            os_name = platform.system()

        if os_name not in ["Darwin", "Windows", "Linux"]:
            raise ValueError(f"Unsupported operating system: {os_name}")

        return [
            ToolDetectorFactory.create_cursor_detector(os_name),
            ToolDetectorFactory.create_claude_detector(os_name),
        ]


class CursorRulesExtractorFactory:
    """Factory for creating OS-specific cursor rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseCursorRulesExtractor:
        """
        Create appropriate cursor rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseCursorRulesExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCursorRulesExtractor()
        elif os_name == "Windows":
            return WindowsCursorRulesExtractor()
        elif os_name == "Linux":
            return LinuxCursorRulesExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class ClaudeRulesExtractorFactory:
    """Factory for creating OS-specific Claude Code rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseClaudeRulesExtractor:
        """
        Create appropriate Claude Code rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseClaudeRulesExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClaudeRulesExtractor()
        elif os_name == "Windows":
            return WindowsClaudeRulesExtractor()
        elif os_name == "Linux":
            return LinuxClaudeRulesExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class CursorMCPConfigExtractorFactory:
    """Factory for creating OS-specific Cursor MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseMCPConfigExtractor:
        """
        Create appropriate Cursor MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCursorMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsCursorMCPConfigExtractor()
        elif os_name == "Linux":
            return LinuxCursorMCPConfigExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class ClaudeMCPConfigExtractorFactory:
    """Factory for creating OS-specific Claude Code MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseMCPConfigExtractor:
        """
        Create appropriate Claude Code MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClaudeMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsClaudeMCPConfigExtractor()
        elif os_name == "Linux":
            return LinuxClaudeMCPConfigExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

