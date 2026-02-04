"""
Factory classes for creating OS-specific detectors.

This module provides factory classes that create appropriate detector instances
based on the operating system.
"""

import platform
from typing import Optional

# Base classes
from .coding_tool_base import (
    BaseDeviceIdExtractor,
    BaseToolDetector,
    BaseCursorRulesExtractor,
    BaseClaudeRulesExtractor,
    BaseWindsurfRulesExtractor,
    BaseClineRulesExtractor,
    BaseAntigravityRulesExtractor,
    BaseKiloCodeRulesExtractor,
    BaseGeminiCliRulesExtractor,
    BaseCodexRulesExtractor,
    BaseOpenCodeRulesExtractor,
    BaseMCPConfigExtractor,
    BaseClaudeSettingsExtractor,
)

# macOS - Shared
from .macos import MacOSDeviceIdExtractor, MacOSCursorDetector, MacOSClaudeDetector

# macOS - Cursor
from .macos.cursor.cursor_rules_extractor import MacOSCursorRulesExtractor
from .macos.cursor.mcp_config_extractor import MacOSCursorMCPConfigExtractor

# macOS - Claude Code
from .macos.claude_code.claude_rules_extractor import MacOSClaudeRulesExtractor
from .macos.claude_code.mcp_config_extractor import MacOSClaudeMCPConfigExtractor
from .macos.claude_code.settings_extractor import MacOSClaudeSettingsExtractor

# macOS - Windsurf
from .macos.windsurf.windsurf import MacOSWindsurfDetector
from .macos.windsurf.windsurf_rules_extractor import MacOSWindsurfRulesExtractor
from .macos.windsurf.mcp_config_extractor import MacOSWindsurfMCPConfigExtractor

# macOS - Roo Code
from .macos.roo_code.roo_code import MacOSRooDetector
from .macos.roo_code.mcp_config_extractor import MacOSRooMCPConfigExtractor

# Windows - Roo Code
from .windows.roo_code.roo_code import WindowsRooDetector
from .windows.roo_code.mcp_config_extractor import WindowsRooMCPConfigExtractor

# macOS - Cline
from .macos.cline.cline import MacOSClineDetector
from .macos.cline.cline_rules_extractor import MacOSClineRulesExtractor
from .macos.cline.mcp_config_extractor import MacOSClineMCPConfigExtractor

# macOS - Antigravity
from .macos.antigravity.antigravity import MacOSAntigravityDetector
from .macos.antigravity.antigravity_rules_extractor import MacOSAntigravityRulesExtractor
from .macos.antigravity.mcp_config_extractor import MacOSAntigravityMCPConfigExtractor

# macOS - Kilo Code
from .macos.kilocode.kilocode import MacOSKiloCodeDetector
from .macos.kilocode.kilocode_rules_extractor import MacOSKiloCodeRulesExtractor
from .macos.kilocode.mcp_config_extractor import MacOSKiloCodeMCPConfigExtractor

# macOS - Gemini CLI
from .macos.gemini_cli.gemini_cli import MacOSGeminiCliDetector
from .macos.gemini_cli.gemini_cli_rules_extractor import MacOSGeminiCliRulesExtractor
from .macos.gemini_cli.mcp_config_extractor import MacOSGeminiCliMCPConfigExtractor

# macOS - Codex
from .macos.codex.codex import MacOSCodexDetector
from .macos.codex.codex_rules_extractor import MacOSCodexRulesExtractor
from .macos.codex.mcp_config_extractor import MacOSCodexMCPConfigExtractor

# macOS - Replit
from .macos.replit.replit import MacOSReplitDetector

# Windows - Replit
from .windows.replit.replit import WindowsReplitDetector
# Windows - Codex
from .windows.codex.codex import WindowsCodexDetector
from .windows.codex.codex_rules_extractor import WindowsCodexRulesExtractor
from .windows.codex.mcp_config_extractor import WindowsCodexMCPConfigExtractor

# macOS - OpenCode
from .macos.opencode.opencode import MacOSOpenCodeDetector
from .macos.opencode.opencode_rules_extractor import MacOSOpenCodeRulesExtractor
from .macos.opencode.mcp_config_extractor import MacOSOpenCodeMCPConfigExtractor

# macOS - JetBrains
from .macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from .macos.jetbrains.mcp_config_extractor import MacOSJetBrainsMCPConfigExtractor

# Windows - JetBrains
from .windows.jetbrains.jetbrains import WindowsJetBrainsDetector
from .windows.jetbrains.mcp_config_extractor import WindowsJetBrainsMCPConfigExtractor

# Windows - OpenCode
from .windows.opencode.opencode import WindowsOpenCodeDetector
from .windows.opencode.opencode_rules_extractor import WindowsOpenCodeRulesExtractor
from .windows.opencode.mcp_config_extractor import WindowsOpenCodeMCPConfigExtractor

# Windows - Gemini CLI
from .windows.gemini_cli.gemini_cli import WindowsGeminiCliDetector
from .windows.gemini_cli.gemini_cli_rules_extractor import WindowsGeminiCliRulesExtractor
from .windows.gemini_cli.mcp_config_extractor import WindowsGeminiCliMCPConfigExtractor

# Windows - Shared
from .windows import WindowsDeviceIdExtractor, WindowsCursorDetector, WindowsClaudeDetector

# Windows - Cursor
from .windows.cursor.cursor_rules_extractor import WindowsCursorRulesExtractor
from .windows.cursor.mcp_config_extractor import WindowsCursorMCPConfigExtractor

# Windows - Claude Code
from .windows.claude_code.claude_rules_extractor import WindowsClaudeRulesExtractor
from .windows.claude_code.mcp_config_extractor import WindowsClaudeMCPConfigExtractor
from .windows.claude_code.settings_extractor import WindowsClaudeSettingsExtractor

# Windows - Windsurf
from .windows.windsurf.windsurf import WindowsWindsurfDetector
from .windows.windsurf.windsurf_rules_extractor import WindowsWindsurfRulesExtractor
from .windows.windsurf.mcp_config_extractor import WindowsWindsurfMCPConfigExtractor

# Windows - Antigravity
from .windows.antigravity.antigravity import WindowsAntigravityDetector
from .windows.antigravity.antigravity_rules_extractor import WindowsAntigravityRulesExtractor
from .windows.antigravity.mcp_config_extractor import WindowsAntigravityMCPConfigExtractor

# Windows - Cline
from .windows.cline.cline import WindowsClineDetector
from .windows.cline.cline_rules_extractor import WindowsClineRulesExtractor
from .windows.cline.mcp_config_extractor import WindowsClineMCPConfigExtractor

# Windows - Kilo Code
from .windows.kilocode.kilocode import WindowsKiloCodeDetector
from .windows.kilocode.kilocode_rules_extractor import WindowsKiloCodeRulesExtractor
from .windows.kilocode.mcp_config_extractor import WindowsKiloCodeMCPConfigExtractor


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
    def create_roo_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Roo Code detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSRooDetector()
        elif os_name == "Windows":
            return WindowsRooDetector()
        else:
            return None

    @staticmethod
    def create_cline_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Cline detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClineDetector()
        elif os_name == "Windows":
            return WindowsClineDetector()
        else:
            return None

    @staticmethod
    def create_antigravity_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Antigravity detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSAntigravityDetector()
        elif os_name == "Windows":
            return WindowsAntigravityDetector()
        else:
            return None

    @staticmethod
    def create_kilocode_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Kilo Code detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSKiloCodeDetector()
        elif os_name == "Windows":
            return WindowsKiloCodeDetector()
        else:
            return None

    @staticmethod
    def create_gemini_cli_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Gemini CLI detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSGeminiCliDetector()
        elif os_name == "Windows":
            return WindowsGeminiCliDetector()
        else:
            return None

    @staticmethod
    def create_codex_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Codex detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCodexDetector()
        elif os_name == "Windows":
            return WindowsCodexDetector()
        else:
            return None

    @staticmethod
    def create_replit_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Replit detector for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSReplitDetector()
        elif os_name == "Windows":
            return WindowsReplitDetector()
        else:
            return None

    @staticmethod
    def create_opencode_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate OpenCode detector for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSOpenCodeDetector()
        elif os_name == "Windows":
            return WindowsOpenCodeDetector()
        else:
            return None

    @staticmethod
    def create_jetbrains_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate JetBrains IDEs detector for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSJetBrainsDetector()
        elif os_name == "Windows":
            return WindowsJetBrainsDetector()
        else:
            return None

    @staticmethod
    def create_all_tool_detectors(os_name: Optional[str] = None) -> list:
        """
        Create all supported tool detectors for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            List of BaseToolDetector instances (None values are filtered out)
        """
        if os_name is None:
            os_name = platform.system()

        detectors = [
            ToolDetectorFactory.create_cursor_detector(os_name),
            ToolDetectorFactory.create_claude_detector(os_name),
            ToolDetectorFactory.create_windsurf_detector(os_name),
            ToolDetectorFactory.create_roo_detector(os_name),
        ]
        
        # Add Cline detector for macOS and Windows
        cline_detector = ToolDetectorFactory.create_cline_detector(os_name)
        if cline_detector is not None:
            detectors.append(cline_detector)
        
        # Add Antigravity detector for both macOS and Windows
        antigravity_detector = ToolDetectorFactory.create_antigravity_detector(os_name)
        if antigravity_detector is not None:
            detectors.append(antigravity_detector)
        
        # Add Kilo Code detector for macOS and Windows
        kilocode_detector = ToolDetectorFactory.create_kilocode_detector(os_name)
        if kilocode_detector is not None:
            detectors.append(kilocode_detector)
        
        # Add Gemini CLI detector for macOS and Windows
        gemini_cli_detector = ToolDetectorFactory.create_gemini_cli_detector(os_name)
        if gemini_cli_detector is not None:
            detectors.append(gemini_cli_detector)
        
        # Add Codex detector for macOS
        codex_detector = ToolDetectorFactory.create_codex_detector(os_name)
        if codex_detector is not None:
            detectors.append(codex_detector)
        
        # Add Replit detector for macOS
        replit_detector = ToolDetectorFactory.create_replit_detector(os_name)
        if replit_detector is not None:
            detectors.append(replit_detector)
        # Add OpenCode detector for macOS
        opencode_detector = ToolDetectorFactory.create_opencode_detector(os_name)
        if opencode_detector is not None:
            detectors.append(opencode_detector)

        # Add JetBrains detector for macOS
        jetbrains_detector = ToolDetectorFactory.create_jetbrains_detector(os_name)
        if jetbrains_detector is not None:
            detectors.append(jetbrains_detector)

        # Filter out None values
        return [detector for detector in detectors if detector is not None]


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


class ClaudeSettingsExtractorFactory:
    """Factory for creating OS-specific Claude Code settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseClaudeSettingsExtractor:
        """
        Create appropriate Claude Code settings extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseClaudeSettingsExtractor instance
            
        Raises:
            ValueError: If OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClaudeSettingsExtractor()
        elif os_name == "Windows":
            return WindowsClaudeSettingsExtractor()
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
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Roo Code MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSRooMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsRooMCPConfigExtractor()
        else:
            return None


class ClineRulesExtractorFactory:
    """Factory for creating OS-specific Cline rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseClineRulesExtractor]:
        """
        Create appropriate Cline rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseClineRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClineRulesExtractor()
        elif os_name == "Windows":
            return WindowsClineRulesExtractor()
        else:
            return None


class ClineMCPConfigExtractorFactory:
    """Factory for creating OS-specific Cline MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Cline MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSClineMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsClineMCPConfigExtractor()
        else:
            return None


class AntigravityRulesExtractorFactory:
    """Factory for creating OS-specific Antigravity rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseAntigravityRulesExtractor]:
        """
        Create appropriate Antigravity rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseAntigravityRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSAntigravityRulesExtractor()
        elif os_name == "Windows":
            return WindowsAntigravityRulesExtractor()
        else:
            return None


class AntigravityMCPConfigExtractorFactory:
    """Factory for creating OS-specific Antigravity MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Antigravity MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSAntigravityMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsAntigravityMCPConfigExtractor()
        else:
            return None


class KiloCodeRulesExtractorFactory:
    """Factory for creating OS-specific Kilo Code rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseKiloCodeRulesExtractor]:
        """
        Create appropriate Kilo Code rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseKiloCodeRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSKiloCodeRulesExtractor()
        elif os_name == "Windows":
            return WindowsKiloCodeRulesExtractor()
        else:
            return None


class KiloCodeMCPConfigExtractorFactory:
    """Factory for creating OS-specific Kilo Code MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Kilo Code MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSKiloCodeMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsKiloCodeMCPConfigExtractor()
        else:
            return None


class GeminiCliRulesExtractorFactory:
    """Factory for creating OS-specific Gemini CLI rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseGeminiCliRulesExtractor]:
        """
        Create appropriate Gemini CLI rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseGeminiCliRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSGeminiCliRulesExtractor()
        elif os_name == "Windows":
            return WindowsGeminiCliRulesExtractor()
        else:
            return None


class GeminiCliMCPConfigExtractorFactory:
    """Factory for creating OS-specific Gemini CLI MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Gemini CLI MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSGeminiCliMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsGeminiCliMCPConfigExtractor()
        else:
            return None


class CodexRulesExtractorFactory:
    """Factory for creating OS-specific Codex rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCodexRulesExtractor]:
        """
        Create appropriate Codex rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseCodexRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCodexRulesExtractor()
        elif os_name == "Windows":
            return WindowsCodexRulesExtractor()
        else:
            return None


class CodexMCPConfigExtractorFactory:
    """Factory for creating OS-specific Codex MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Codex MCP config extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSCodexMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsCodexMCPConfigExtractor()
        else:
            return None


class OpenCodeRulesExtractorFactory:
    """Factory for creating OS-specific OpenCode rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseOpenCodeRulesExtractor]:
        """
        Create appropriate OpenCode rules extractor for the OS.
        
        Args:
            os_name: Operating system name (defaults to current OS)
            
        Returns:
            BaseOpenCodeRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSOpenCodeRulesExtractor()
        elif os_name == "Windows":
            return WindowsOpenCodeRulesExtractor()
        else:
            return None


class OpenCodeMCPConfigExtractorFactory:
    """Factory for creating OS-specific OpenCode MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate OpenCode MCP config extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSOpenCodeMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsOpenCodeMCPConfigExtractor()
        else:
            return None


class JetBrainsMCPConfigExtractorFactory:
    """Factory for creating OS-specific JetBrains MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate JetBrains MCP config extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            return MacOSJetBrainsMCPConfigExtractor()
        elif os_name == "Windows":
            return WindowsJetBrainsMCPConfigExtractor()
        else:
            return None
