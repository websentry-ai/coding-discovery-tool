"""
Factory classes for creating OS-specific detectors.

This module provides factory classes that create appropriate detector instances
based on the operating system.
"""

import platform
from typing import Optional

from .coding_tool_base import BaseDeviceIdExtractor, BaseToolDetector, BaseCursorRulesExtractor, BaseClaudeRulesExtractor, BaseWindsurfRulesExtractor, BaseKiloCodeRulesExtractor, BaseMCPConfigExtractor
from .macos import MacOSDeviceIdExtractor, MacOSCursorDetector, MacOSClaudeDetector
from .macos.cursor.cursor_rules_extractor import MacOSCursorRulesExtractor
from .macos.cursor.mcp_config_extractor import MacOSCursorMCPConfigExtractor
from .macos.claude_code.claude_rules_extractor import MacOSClaudeRulesExtractor
from .macos.claude_code.mcp_config_extractor import MacOSClaudeMCPConfigExtractor
from .macos.windsurf.windsurf import MacOSWindsurfDetector
from .macos.windsurf.windsurf_rules_extractor import MacOSWindsurfRulesExtractor
from .macos.windsurf.mcp_config_extractor import MacOSWindsurfMCPConfigExtractor
from .macos.roo_code.roo_code import MacOSRooDetector
from .macos.roo_code.mcp_config_extractor import MacOSRooMCPConfigExtractor
from .macos.kilocode.kilocode import MacOSKiloCodeDetector
from .macos.kilocode.kilocode_rules_extractor import MacOSKiloCodeRulesExtractor
from .macos.kilocode.mcp_config_extractor import MacOSKiloCodeMCPConfigExtractor
from .windows import WindowsDeviceIdExtractor, WindowsCursorDetector, WindowsClaudeDetector
from .windows.cursor.cursor_rules_extractor import WindowsCursorRulesExtractor
from .windows.cursor.mcp_config_extractor import WindowsCursorMCPConfigExtractor
from .windows.claude_code.claude_rules_extractor import WindowsClaudeRulesExtractor
from .windows.claude_code.mcp_config_extractor import WindowsClaudeMCPConfigExtractor
from .windows.windsurf.windsurf import WindowsWindsurfDetector
from .windows.windsurf.windsurf_rules_extractor import WindowsWindsurfRulesExtractor
from .windows.windsurf.mcp_config_extractor import WindowsWindsurfMCPConfigExtractor


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
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

    @staticmethod
    def create_windsurf_detector(os_name: Optional[str] = None) -> BaseToolDetector:
        """
        Create appropriate Windsurf detector for the OS.
        
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
            return MacOSWindsurfDetector()
        elif os_name == "Windows":
            return WindowsWindsurfDetector()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

    @staticmethod
    def create_roo_detector(os_name: Optional[str] = None) -> BaseToolDetector:
        """
        Create appropriate Roo Code detector for the OS.
        
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
            return MacOSRooDetector()

    @staticmethod
    def create_kilocode_detector(os_name: Optional[str] = None) -> BaseToolDetector:
        """
        Create appropriate Kilo Code detector for the OS.
        
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
            return MacOSKiloCodeDetector()

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

        if os_name not in ["Darwin", "Windows"]:
            raise ValueError(f"Unsupported operating system: {os_name}")

        detectors = [
            ToolDetectorFactory.create_cursor_detector(os_name),
            ToolDetectorFactory.create_claude_detector(os_name),
            ToolDetectorFactory.create_windsurf_detector(os_name),
            ToolDetectorFactory.create_roo_detector(os_name),
        ]
        
        # Add Kilo Code detector for macOS
        if os_name == "Darwin":
            detectors.append(ToolDetectorFactory.create_kilocode_detector(os_name))
        
        return detectors


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
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class WindsurfRulesExtractorFactory:
    """Factory for creating OS-specific Windsurf rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseWindsurfRulesExtractor:
        """
        Create appropriate Windsurf rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseWindsurfRulesExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSWindsurfRulesExtractor()
        elif os_name == "Windows":
            return WindowsWindsurfRulesExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class WindsurfMCPConfigExtractorFactory:
    """Factory for creating OS-specific Windsurf MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseMCPConfigExtractor:
        """
        Create appropriate Windsurf MCP config extractor for the OS.
        
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
            return MacOSWindsurfMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsWindsurfMCPConfigExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class RooMCPConfigExtractorFactory:
    """Factory for creating OS-specific Roo Code MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseMCPConfigExtractor:
        """
        Create appropriate Roo Code MCP config extractor for the OS.
        
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
            return MacOSRooMCPConfigExtractor()


class KiloCodeRulesExtractorFactory:
    """Factory for creating OS-specific Kilo Code rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseKiloCodeRulesExtractor:
        """
        Create appropriate Kilo Code rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseKiloCodeRulesExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSKiloCodeRulesExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class KiloCodeMCPConfigExtractorFactory:
    """Factory for creating OS-specific Kilo Code MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseMCPConfigExtractor:
        """
        Create appropriate Kilo Code MCP config extractor for the OS.
        
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
            return MacOSKiloCodeMCPConfigExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")
