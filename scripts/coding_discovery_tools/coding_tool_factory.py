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
    BaseRooRulesExtractor,
    BaseAntigravityRulesExtractor,
    BaseKiloCodeRulesExtractor,
    BaseGeminiCliRulesExtractor,
    BaseCodexRulesExtractor,
    BaseOpenCodeRulesExtractor,
    BaseCursorCliRulesExtractor,
    BaseCopilotCliRulesExtractor,
    BaseCopilotCliSettingsExtractor,
    BaseCopilotCliSkillsExtractor,
    BaseAugmentRulesExtractor,
    BaseAugmentSettingsExtractor,
    BaseAugmentSkillsExtractor,
    BaseMCPConfigExtractor,
    BaseClaudeSettingsExtractor,
    BaseCursorSettingsExtractor,
    BaseOpenClawDetector,
    BaseCopilotDetector,
    BaseJunieRulesExtractor,
    BaseClaudeSkillsExtractor,
    BaseClaudeCoworkSkillsExtractor,
    BaseCursorSkillsExtractor,
    BaseClineSkillsExtractor,
    BaseCodexSkillsExtractor,
    BaseGeminiCliSkillsExtractor,
    BaseJunieSkillsExtractor,
    BaseKiloCodeSkillsExtractor,
    BaseOpenCodeSkillsExtractor,
    BaseRooSkillsExtractor,
    BaseReplitSkillsExtractor,
    BaseWindsurfSkillsExtractor,
)

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
            from .macos import MacOSDeviceIdExtractor
            return MacOSDeviceIdExtractor()
        elif os_name == "Windows":
            from .windows import WindowsDeviceIdExtractor
            return WindowsDeviceIdExtractor()
        elif os_name == "Linux":
            from .linux import LinuxDeviceIdExtractor
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
            from .macos import MacOSCursorDetector
            return MacOSCursorDetector()
        elif os_name == "Windows":
            from .windows import WindowsCursorDetector
            return WindowsCursorDetector()
        elif os_name == "Linux":
            from .linux import LinuxCursorDetector
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
            from .macos import MacOSClaudeDetector
            return MacOSClaudeDetector()
        elif os_name == "Windows":
            from .windows import WindowsClaudeDetector
            return WindowsClaudeDetector()
        elif os_name == "Linux":
            from .linux import LinuxClaudeDetector
            return LinuxClaudeDetector()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")

    @staticmethod
    def create_claude_cowork_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Claude Cowork detector for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos import MacOSClaudeCoworkDetector
            return MacOSClaudeCoworkDetector()
        elif os_name == "Windows":
            from .windows import WindowsClaudeCoworkDetector
            return WindowsClaudeCoworkDetector()
        elif os_name == "Linux":
            from .linux import LinuxClaudeCoworkDetector
            return LinuxClaudeCoworkDetector()
        else:
            return None

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
            from .macos.windsurf.windsurf import MacOSWindsurfDetector
            return MacOSWindsurfDetector()
        elif os_name == "Windows":
            from .windows.windsurf.windsurf import WindowsWindsurfDetector
            return WindowsWindsurfDetector()
        elif os_name == "Linux":
            from .linux import LinuxWindsurfDetector
            return LinuxWindsurfDetector()
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
            from .macos.roo_code.roo_code import MacOSRooDetector
            return MacOSRooDetector()
        elif os_name == "Windows":
            from .windows.roo_code.roo_code import WindowsRooDetector
            return WindowsRooDetector()
        elif os_name == "Linux":
            from .linux import LinuxRooDetector
            return LinuxRooDetector()
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
            from .macos.cline.cline import MacOSClineDetector
            return MacOSClineDetector()
        elif os_name == "Windows":
            from .windows.cline.cline import WindowsClineDetector
            return WindowsClineDetector()
        elif os_name == "Linux":
            from .linux import LinuxClineDetector
            return LinuxClineDetector()
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
            from .macos.antigravity.antigravity import MacOSAntigravityDetector
            return MacOSAntigravityDetector()
        elif os_name == "Windows":
            from .windows.antigravity.antigravity import WindowsAntigravityDetector
            return WindowsAntigravityDetector()
        elif os_name == "Linux":
            from .linux import LinuxAntigravityDetector
            return LinuxAntigravityDetector()
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
            from .macos.kilocode.kilocode import MacOSKiloCodeDetector
            return MacOSKiloCodeDetector()
        elif os_name == "Windows":
            from .windows.kilocode.kilocode import WindowsKiloCodeDetector
            return WindowsKiloCodeDetector()
        elif os_name == "Linux":
            from .linux import LinuxKiloCodeDetector
            return LinuxKiloCodeDetector()
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
            from .macos.gemini_cli.gemini_cli import MacOSGeminiCliDetector
            return MacOSGeminiCliDetector()
        elif os_name == "Windows":
            from .windows.gemini_cli.gemini_cli import WindowsGeminiCliDetector
            return WindowsGeminiCliDetector()
        elif os_name == "Linux":
            from .linux import LinuxGeminiCliDetector
            return LinuxGeminiCliDetector()
        else:
            return None

    @staticmethod
    def create_cursor_cli_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Cursor CLI detector for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseToolDetector instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor_cli.cursor_cli import MacOSCursorCliDetector
            return MacOSCursorCliDetector()
        elif os_name == "Windows":
            from .windows.cursor_cli.cursor_cli import WindowsCursorCliDetector
            return WindowsCursorCliDetector()
        elif os_name == "Linux":
            from .linux import LinuxCursorCliDetector
            return LinuxCursorCliDetector()
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
            from .macos.codex.codex import MacOSCodexDetector
            return MacOSCodexDetector()
        elif os_name == "Windows":
            from .windows.codex.codex import WindowsCodexDetector
            return WindowsCodexDetector()
        elif os_name == "Linux":
            from .linux import LinuxCodexDetector
            return LinuxCodexDetector()
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
            from .macos.replit.replit import MacOSReplitDetector
            return MacOSReplitDetector()
        elif os_name == "Windows":
            from .windows.replit.replit import WindowsReplitDetector
            return WindowsReplitDetector()
        elif os_name == "Linux":
            from .linux import LinuxReplitDetector
            return LinuxReplitDetector()
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
            from .macos.opencode.opencode import MacOSOpenCodeDetector
            return MacOSOpenCodeDetector()
        elif os_name == "Windows":
            from .windows.opencode.opencode import WindowsOpenCodeDetector
            return WindowsOpenCodeDetector()
        elif os_name == "Linux":
            from .linux import LinuxOpenCodeDetector
            return LinuxOpenCodeDetector()
        else:
            return None

    @staticmethod
    def create_openclaw_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate OpenClaw detector for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.openclaw.detect_openclaw import MacOSOpenClawDetector
            return MacOSOpenClawDetector()
        elif os_name == "Windows":
            from .windows.openclaw.detect_openclaw import WindowsOpenClawDetector
            return WindowsOpenClawDetector()
        elif os_name == "Linux":
            from .linux import LinuxOpenClawDetector
            return LinuxOpenClawDetector()
        else:
            return None

    @staticmethod
    def create_copilot_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Copilot detector for the OS.
        """
        if os_name is None:
            os_name = platform.system()
        if os_name == "Darwin":
            from .macos.github_copilot.detect_copilot import MacOSCopilotDetector
            return MacOSCopilotDetector()
        elif os_name == "Windows":
            from .windows.github_copilot.detect_copilot import WindowsGitHubCopilotDetector
            return WindowsGitHubCopilotDetector()
        elif os_name == "Linux":
            from .linux import LinuxCopilotDetector
            return LinuxCopilotDetector()
        else:
            return None

    @staticmethod
    def create_copilot_cli_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate GitHub Copilot CLI detector for the OS.
        """
        if os_name is None:
            os_name = platform.system()
        if os_name == "Darwin":
            from .macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector
            return MacOSCopilotCliDetector()
        elif os_name == "Windows":
            from .windows.copilot_cli.copilot_cli import WindowsCopilotCliDetector
            return WindowsCopilotCliDetector()
        elif os_name == "Linux":
            from .linux import LinuxCopilotCliDetector
            return LinuxCopilotCliDetector()
        else:
            return None

    @staticmethod
    def create_augment_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Create appropriate Augment Code detector for the OS.
        """
        if os_name is None:
            os_name = platform.system()
        if os_name == "Darwin":
            from .macos.augment.augment import MacOSAugmentDetector
            return MacOSAugmentDetector()
        elif os_name == "Windows":
            from .windows.augment.augment import WindowsAugmentDetector
            return WindowsAugmentDetector()
        elif os_name == "Linux":
            from .linux import LinuxAugmentDetector
            return LinuxAugmentDetector()
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
            from .macos.jetbrains.jetbrains import MacOSJetBrainsDetector
            return MacOSJetBrainsDetector()
        elif os_name == "Windows":
            from .windows.jetbrains.jetbrains import WindowsJetBrainsDetector
            return WindowsJetBrainsDetector()
        elif os_name == "Linux":
            from .linux import LinuxJetBrainsDetector
            return LinuxJetBrainsDetector()
        else:
            return None

    @staticmethod
    def create_junie_detector(os_name: Optional[str] = None) -> Optional[BaseToolDetector]:
        """
        Junie detector for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.junie.junie import MacOSJunieDetector
            return MacOSJunieDetector()
        elif os_name == "Linux":
            from .linux import LinuxJunieDetector
            return LinuxJunieDetector()
        elif os_name == "Windows":
            from .windows.junie.junie import WindowsJunieDetector
            return WindowsJunieDetector()
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

        # Add Claude Cowork detector for macOS, Windows, and Linux
        claude_cowork_detector = ToolDetectorFactory.create_claude_cowork_detector(os_name)
        if claude_cowork_detector is not None:
            detectors.append(claude_cowork_detector)
        
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

        # Add Cursor CLI detector for macOS and Windows
        cursor_cli_detector = ToolDetectorFactory.create_cursor_cli_detector(os_name)
        if cursor_cli_detector is not None:
            detectors.append(cursor_cli_detector)

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

        openclaw_detector = ToolDetectorFactory.create_openclaw_detector(os_name)
        if openclaw_detector is not None:
            detectors.append(openclaw_detector)

        copilot_detector = ToolDetectorFactory.create_copilot_detector(os_name)
        if copilot_detector is not None:
            detectors.append(copilot_detector)

        # Add GitHub Copilot CLI detector (macOS + Windows)
        copilot_cli_detector = ToolDetectorFactory.create_copilot_cli_detector(os_name)
        if copilot_cli_detector is not None:
            detectors.append(copilot_cli_detector)

        # Add Augment Code detector (macOS + Windows + Linux)
        augment_detector = ToolDetectorFactory.create_augment_detector(os_name)
        if augment_detector is not None:
            detectors.append(augment_detector)

        # Add JetBrains detector for macOS
        jetbrains_detector = ToolDetectorFactory.create_jetbrains_detector(os_name)
        if jetbrains_detector is not None:
            detectors.append(jetbrains_detector)

        junie_detector = ToolDetectorFactory.create_junie_detector(os_name)
        if junie_detector is not None:
            detectors.append(junie_detector)

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
            from .macos.cursor.cursor_rules_extractor import MacOSCursorRulesExtractor
            return MacOSCursorRulesExtractor()
        elif os_name == "Windows":
            from .windows.cursor.cursor_rules_extractor import WindowsCursorRulesExtractor
            return WindowsCursorRulesExtractor()
        elif os_name == "Linux":
            from .linux.cursor.cursor_rules_extractor import LinuxCursorRulesExtractor
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
            from .macos.claude_code.claude_rules_extractor import MacOSClaudeRulesExtractor
            return MacOSClaudeRulesExtractor()
        elif os_name == "Windows":
            from .windows.claude_code.claude_rules_extractor import WindowsClaudeRulesExtractor
            return WindowsClaudeRulesExtractor()
        elif os_name == "Linux":
            from .linux.claude_code.claude_rules_extractor import LinuxClaudeRulesExtractor
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
            from .macos.cursor.mcp_config_extractor import MacOSCursorMCPConfigExtractor
            return MacOSCursorMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.cursor.mcp_config_extractor import WindowsCursorMCPConfigExtractor
            return WindowsCursorMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux.cursor.mcp_config_extractor import LinuxCursorMCPConfigExtractor
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
            from .macos.claude_code.mcp_config_extractor import MacOSClaudeMCPConfigExtractor
            return MacOSClaudeMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.claude_code.mcp_config_extractor import WindowsClaudeMCPConfigExtractor
            return WindowsClaudeMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux.claude_code.mcp_config_extractor import LinuxClaudeMCPConfigExtractor
            return LinuxClaudeMCPConfigExtractor()
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
            from .macos.claude_code.settings_extractor import MacOSClaudeSettingsExtractor
            return MacOSClaudeSettingsExtractor()
        elif os_name == "Windows":
            from .windows.claude_code.settings_extractor import WindowsClaudeSettingsExtractor
            return WindowsClaudeSettingsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClaudeSettingsExtractor
            return LinuxClaudeSettingsExtractor()
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")


class CursorSettingsExtractorFactory:
    """Factory for creating OS-specific Cursor IDE settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> BaseCursorSettingsExtractor:
        """
        Create appropriate Cursor IDE settings extractor for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor.settings_extractor import MacOSCursorSettingsExtractor
            return MacOSCursorSettingsExtractor()
        elif os_name == "Windows":
            from .windows.cursor.settings_extractor import WindowsCursorSettingsExtractor
            return WindowsCursorSettingsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCursorSettingsExtractor
            return LinuxCursorSettingsExtractor()
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
            from .macos.windsurf.windsurf_rules_extractor import MacOSWindsurfRulesExtractor
            return MacOSWindsurfRulesExtractor()
        elif os_name == "Windows":
            from .windows.windsurf.windsurf_rules_extractor import WindowsWindsurfRulesExtractor
            return WindowsWindsurfRulesExtractor()
        elif os_name == "Linux":
            from .linux.windsurf.windsurf_rules_extractor import LinuxWindsurfRulesExtractor
            return LinuxWindsurfRulesExtractor()
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
            from .macos.windsurf.mcp_config_extractor import MacOSWindsurfMCPConfigExtractor
            return MacOSWindsurfMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.windsurf.mcp_config_extractor import WindowsWindsurfMCPConfigExtractor
            return WindowsWindsurfMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux.windsurf.mcp_config_extractor import LinuxWindsurfMCPConfigExtractor
            return LinuxWindsurfMCPConfigExtractor()
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
            from .macos.roo_code.mcp_config_extractor import MacOSRooMCPConfigExtractor
            return MacOSRooMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.roo_code.mcp_config_extractor import WindowsRooMCPConfigExtractor
            return WindowsRooMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxRooMCPConfigExtractor
            return LinuxRooMCPConfigExtractor()
        else:
            return None


class RooRulesExtractorFactory:
    """Factory for creating OS-specific Roo Code rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseRooRulesExtractor]:
        """
        Create Roo Code rules extractor for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.roo_code.roo_code_rules_extractor import MacOSRooRulesExtractor
            return MacOSRooRulesExtractor()
        elif os_name == "Windows":
            from .windows.roo_code.roo_code_rules_extractor import WindowsRooRulesExtractor
            return WindowsRooRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxRooRulesExtractor
            return LinuxRooRulesExtractor()
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
            from .macos.cline.cline_rules_extractor import MacOSClineRulesExtractor
            return MacOSClineRulesExtractor()
        elif os_name == "Windows":
            from .windows.cline.cline_rules_extractor import WindowsClineRulesExtractor
            return WindowsClineRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClineRulesExtractor
            return LinuxClineRulesExtractor()
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
            from .macos.cline.mcp_config_extractor import MacOSClineMCPConfigExtractor
            return MacOSClineMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.cline.mcp_config_extractor import WindowsClineMCPConfigExtractor
            return WindowsClineMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClineMCPConfigExtractor
            return LinuxClineMCPConfigExtractor()
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
            from .macos.antigravity.antigravity_rules_extractor import MacOSAntigravityRulesExtractor
            return MacOSAntigravityRulesExtractor()
        elif os_name == "Windows":
            from .windows.antigravity.antigravity_rules_extractor import WindowsAntigravityRulesExtractor
            return WindowsAntigravityRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAntigravityRulesExtractor
            return LinuxAntigravityRulesExtractor()
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
            from .macos.antigravity.mcp_config_extractor import MacOSAntigravityMCPConfigExtractor
            return MacOSAntigravityMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.antigravity.mcp_config_extractor import WindowsAntigravityMCPConfigExtractor
            return WindowsAntigravityMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAntigravityMCPConfigExtractor
            return LinuxAntigravityMCPConfigExtractor()
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
            from .macos.kilocode.kilocode_rules_extractor import MacOSKiloCodeRulesExtractor
            return MacOSKiloCodeRulesExtractor()
        elif os_name == "Windows":
            from .windows.kilocode.kilocode_rules_extractor import WindowsKiloCodeRulesExtractor
            return WindowsKiloCodeRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxKiloCodeRulesExtractor
            return LinuxKiloCodeRulesExtractor()
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
            from .macos.kilocode.mcp_config_extractor import MacOSKiloCodeMCPConfigExtractor
            return MacOSKiloCodeMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.kilocode.mcp_config_extractor import WindowsKiloCodeMCPConfigExtractor
            return WindowsKiloCodeMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxKiloCodeMCPConfigExtractor
            return LinuxKiloCodeMCPConfigExtractor()
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
            from .macos.gemini_cli.gemini_cli_rules_extractor import MacOSGeminiCliRulesExtractor
            return MacOSGeminiCliRulesExtractor()
        elif os_name == "Windows":
            from .windows.gemini_cli.gemini_cli_rules_extractor import WindowsGeminiCliRulesExtractor
            return WindowsGeminiCliRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxGeminiCliRulesExtractor
            return LinuxGeminiCliRulesExtractor()
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
            from .macos.gemini_cli.mcp_config_extractor import MacOSGeminiCliMCPConfigExtractor
            return MacOSGeminiCliMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.gemini_cli.mcp_config_extractor import WindowsGeminiCliMCPConfigExtractor
            return WindowsGeminiCliMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxGeminiCliMCPConfigExtractor
            return LinuxGeminiCliMCPConfigExtractor()
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
            from .macos.codex.codex_rules_extractor import MacOSCodexRulesExtractor
            return MacOSCodexRulesExtractor()
        elif os_name == "Windows":
            from .windows.codex.codex_rules_extractor import WindowsCodexRulesExtractor
            return WindowsCodexRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCodexRulesExtractor
            return LinuxCodexRulesExtractor()
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
            from .macos.codex.mcp_config_extractor import MacOSCodexMCPConfigExtractor
            return MacOSCodexMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.codex.mcp_config_extractor import WindowsCodexMCPConfigExtractor
            return WindowsCodexMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCodexMCPConfigExtractor
            return LinuxCodexMCPConfigExtractor()
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
            from .macos.opencode.opencode_rules_extractor import MacOSOpenCodeRulesExtractor
            return MacOSOpenCodeRulesExtractor()
        elif os_name == "Windows":
            from .windows.opencode.opencode_rules_extractor import WindowsOpenCodeRulesExtractor
            return WindowsOpenCodeRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxOpenCodeRulesExtractor
            return LinuxOpenCodeRulesExtractor()
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
            from .macos.opencode.mcp_config_extractor import MacOSOpenCodeMCPConfigExtractor
            return MacOSOpenCodeMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.opencode.mcp_config_extractor import WindowsOpenCodeMCPConfigExtractor
            return WindowsOpenCodeMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxOpenCodeMCPConfigExtractor
            return LinuxOpenCodeMCPConfigExtractor()
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
            from .macos.jetbrains.mcp_config_extractor import MacOSJetBrainsMCPConfigExtractor
            return MacOSJetBrainsMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.jetbrains.mcp_config_extractor import WindowsJetBrainsMCPConfigExtractor
            return WindowsJetBrainsMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxJetBrainsMCPConfigExtractor
            return LinuxJetBrainsMCPConfigExtractor()
        else:
            return None


class GitHubCopilotMCPConfigExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create GitHub Copilot MCP config extractor for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.github_copilot.mcp_config_extractor import MacOSGitHubCopilotMCPConfigExtractor
            return MacOSGitHubCopilotMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.github_copilot.mcp_config_extractor import WindowsGitHubCopilotMCPConfigExtractor
            return WindowsGitHubCopilotMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxGitHubCopilotMCPConfigExtractor
            return LinuxGitHubCopilotMCPConfigExtractor()
        else:
            return None


class CopilotCliMCPConfigExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot CLI MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create GitHub Copilot CLI MCP config extractor for the OS.

        The extraction logic is OS-agnostic (the all-users scan is handled by
        the shared root-support helper), so the Windows extractor is a thin
        subclass of the macOS one — see WindowsCopilotCliMCPConfigExtractor.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.copilot_cli.mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor
            return MacOSCopilotCliMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.copilot_cli.mcp_config_extractor import WindowsCopilotCliMCPConfigExtractor
            return WindowsCopilotCliMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCopilotCliMCPConfigExtractor
            return LinuxCopilotCliMCPConfigExtractor()
        else:
            return None


class CopilotCliRulesExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot CLI rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCopilotCliRulesExtractor]:
        """
        Create GitHub Copilot CLI rules extractor for the OS.

        The 6-source detection logic and the depth-bounded walk are shared in
        the macOS class; the Windows subclass overrides only the OS-specific
        seams (privilege check, all-users scan, filesystem root, top-level
        enumeration, system-dir skip) — see WindowsCopilotCliRulesExtractor.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.copilot_cli.copilot_cli_rules_extractor import MacOSCopilotCliRulesExtractor
            return MacOSCopilotCliRulesExtractor()
        elif os_name == "Windows":
            from .windows.copilot_cli.copilot_cli_rules_extractor import WindowsCopilotCliRulesExtractor
            return WindowsCopilotCliRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCopilotCliRulesExtractor
            return LinuxCopilotCliRulesExtractor()
        else:
            return None


class CopilotCliSettingsExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot CLI settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCopilotCliSettingsExtractor]:
        """
        Create a GitHub Copilot CLI settings/permissions extractor for the OS.

        Reads the durable user-scope permission config (trusted folders, URL
        allow/deny); the only OS-specific seam is the all-users scan, so the
        Windows extractor is a thin subclass — see WindowsCopilotCliSettingsExtractor.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.copilot_cli.copilot_cli_settings_extractor import MacOSCopilotCliSettingsExtractor
            return MacOSCopilotCliSettingsExtractor()
        elif os_name == "Windows":
            from .windows.copilot_cli.copilot_cli_settings_extractor import WindowsCopilotCliSettingsExtractor
            return WindowsCopilotCliSettingsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCopilotCliSettingsExtractor
            return LinuxCopilotCliSettingsExtractor()
        else:
            return None


class CopilotCliSkillsExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot CLI skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCopilotCliSkillsExtractor]:
        """
        Create a GitHub Copilot CLI skills extractor for the OS.

        Reuses the shared skills engine; macOS and Windows are independent
        implementations (Windows parallelizes the walk with a thread pool) — see
        WindowsCopilotCliSkillsExtractor.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.copilot_cli.copilot_cli_skills_extractor import MacOSCopilotCliSkillsExtractor
            return MacOSCopilotCliSkillsExtractor()
        elif os_name == "Windows":
            from .windows.copilot_cli.copilot_cli_skills_extractor import WindowsCopilotCliSkillsExtractor
            return WindowsCopilotCliSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCopilotCliSkillsExtractor
            return LinuxCopilotCliSkillsExtractor()
        else:
            return None


class AugmentMCPConfigExtractorFactory:
    """Factory for creating OS-specific Augment Code MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create an Augment Code MCP config extractor for the OS.

        The parser + User-scope read are OS-agnostic; the Windows/Linux extractors
        are thin subclasses overriding only the workspace walk.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.augment.augment_mcp_config_extractor import MacOSAugmentMCPConfigExtractor
            return MacOSAugmentMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.augment.augment_mcp_config_extractor import WindowsAugmentMCPConfigExtractor
            return WindowsAugmentMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAugmentMCPConfigExtractor
            return LinuxAugmentMCPConfigExtractor()
        else:
            return None


class AugmentRulesExtractorFactory:
    """Factory for creating OS-specific Augment Code rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseAugmentRulesExtractor]:
        """
        Create an Augment Code rules extractor for the OS.

        The source set + depth-bounded walk are shared in the macOS class; the
        Windows/Linux subclasses override only the OS-specific seams.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.augment.augment_rules_extractor import MacOSAugmentRulesExtractor
            return MacOSAugmentRulesExtractor()
        elif os_name == "Windows":
            from .windows.augment.augment_rules_extractor import WindowsAugmentRulesExtractor
            return WindowsAugmentRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAugmentRulesExtractor
            return LinuxAugmentRulesExtractor()
        else:
            return None


class AugmentSettingsExtractorFactory:
    """Factory for creating OS-specific Augment Code settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseAugmentSettingsExtractor]:
        """
        Create an Augment Code settings/permissions extractor for the OS.

        Parses ``toolPermissions`` + preserves the full settings JSON (incl.
        hooks) in raw_settings; Windows/Linux are thin subclasses overriding the
        all-users scan, managed path, and filesystem seams.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.augment.augment_settings_extractor import MacOSAugmentSettingsExtractor
            return MacOSAugmentSettingsExtractor()
        elif os_name == "Windows":
            from .windows.augment.augment_settings_extractor import WindowsAugmentSettingsExtractor
            return WindowsAugmentSettingsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAugmentSettingsExtractor
            return LinuxAugmentSettingsExtractor()
        else:
            return None


class AugmentSkillsExtractorFactory:
    """Factory for creating OS-specific Augment Code skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseAugmentSkillsExtractor]:
        """
        Create an Augment Code skills extractor for the OS.

        Reuses the shared skills engine; Windows/Linux are thin (single-threaded)
        subclasses overriding only the OS seams.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.augment.augment_skills_extractor import MacOSAugmentSkillsExtractor
            return MacOSAugmentSkillsExtractor()
        elif os_name == "Windows":
            from .windows.augment.augment_skills_extractor import WindowsAugmentSkillsExtractor
            return WindowsAugmentSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxAugmentSkillsExtractor
            return LinuxAugmentSkillsExtractor()
        else:
            return None


class GitHubCopilotRulesExtractorFactory:
    """Factory for creating OS-specific GitHub Copilot rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None):
        """
        Create GitHub Copilot rules extractor for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.github_copilot.copilot_rules_extractor import MacOSGitHubCopilotRulesExtractor
            return MacOSGitHubCopilotRulesExtractor()
        elif os_name == "Windows":
            from .windows.github_copilot.copilot_rules_extractor import WindowsGitHubCopilotRulesExtractor
            return WindowsGitHubCopilotRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxGitHubCopilotRulesExtractor
            return LinuxGitHubCopilotRulesExtractor()
        else:
            return None


class GitHubCopilotSettingsExtractorFactory:
    """Factory for creating OS-specific VS Code GitHub Copilot settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None):
        """Create the VS Code GitHub Copilot settings/permission extractor for the OS."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.github_copilot.settings_extractor import MacOSGitHubCopilotSettingsExtractor
            return MacOSGitHubCopilotSettingsExtractor()
        elif os_name == "Windows":
            from .windows.github_copilot.settings_extractor import WindowsGitHubCopilotSettingsExtractor
            return WindowsGitHubCopilotSettingsExtractor()
        elif os_name == "Linux":
            from .linux.github_copilot.settings_extractor import LinuxGitHubCopilotSettingsExtractor
            return LinuxGitHubCopilotSettingsExtractor()
        else:
            return None


class JunieMCPConfigExtractorFactory:
    """Factory for creating OS-specific Junie MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Junie MCP config extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.junie.mcp_config_extractor import MacOSJunieMCPConfigExtractor
            return MacOSJunieMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxJunieMCPConfigExtractor
            return LinuxJunieMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.junie.mcp_config_extractor import WindowsJunieMCPConfigExtractor
            return WindowsJunieMCPConfigExtractor()
        else:
            return None


class JunieRulesExtractorFactory:
    """Factory for creating OS-specific Junie rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseJunieRulesExtractor]:
        """
        Create appropriate Junie rules extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseJunieRulesExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.junie.junie_rules_extractor import MacOSJunieRulesExtractor
            return MacOSJunieRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxJunieRulesExtractor
            return LinuxJunieRulesExtractor()
        elif os_name == "Windows":
            from .windows.junie.junie_rules_extractor import WindowsJunieRulesExtractor
            return WindowsJunieRulesExtractor()
        else:
            return None


class CursorCliSettingsExtractorFactory:
    """Factory for creating OS-specific Cursor CLI settings extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None):
        """
        Create appropriate Cursor CLI settings extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            Cursor CLI settings extractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor_cli.settings_extractor import MacOSCursorCliSettingsExtractor
            return MacOSCursorCliSettingsExtractor()
        elif os_name == "Windows":
            from .windows.cursor_cli.settings_extractor import WindowsCursorCliSettingsExtractor
            return WindowsCursorCliSettingsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCursorCliSettingsExtractor
            return LinuxCursorCliSettingsExtractor()
        else:
            return None


class CursorCliMCPConfigExtractorFactory:
    """Factory for creating OS-specific Cursor CLI MCP config extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseMCPConfigExtractor]:
        """
        Create appropriate Cursor CLI MCP config extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseMCPConfigExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor_cli.mcp_config_extractor import MacOSCursorCliMCPConfigExtractor
            return MacOSCursorCliMCPConfigExtractor()
        elif os_name == "Windows":
            from .windows.cursor_cli.mcp_config_extractor import WindowsCursorCliMCPConfigExtractor
            return WindowsCursorCliMCPConfigExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCursorCliMCPConfigExtractor
            return LinuxCursorCliMCPConfigExtractor()
        else:
            return None


class CursorCliRulesExtractorFactory:
    """Factory for creating OS-specific Cursor CLI rules extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCursorCliRulesExtractor]:
        """
        Create appropriate Cursor CLI rules extractor for the OS.
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor_cli.cursor_cli_rules_extractor import MacOSCursorCliRulesExtractor
            return MacOSCursorCliRulesExtractor()
        elif os_name == "Windows":
            from .windows.cursor_cli.cursor_cli_rules_extractor import WindowsCursorCliRulesExtractor
            return WindowsCursorCliRulesExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCursorCliRulesExtractor
            return LinuxCursorCliRulesExtractor()
        else:
            return None


class ClaudeSkillsExtractorFactory:
    """Factory for creating OS-specific Claude Code skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseClaudeSkillsExtractor]:
        """
        Create appropriate Claude Code skills extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseClaudeSkillsExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.claude_code.skills_extractor import MacOSClaudeSkillsExtractor
            return MacOSClaudeSkillsExtractor()
        elif os_name == "Windows":
            from .windows.claude_code.skills_extractor import WindowsClaudeSkillsExtractor
            return WindowsClaudeSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClaudeSkillsExtractor
            return LinuxClaudeSkillsExtractor()
        else:
            return None


class CursorSkillsExtractorFactory:
    """Factory for creating OS-specific Cursor skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCursorSkillsExtractor]:
        """
        Create appropriate Cursor skills extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseCursorSkillsExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cursor.skills_extractor import MacOSCursorSkillsExtractor
            return MacOSCursorSkillsExtractor()
        elif os_name == "Windows":
            from .windows.cursor.skills_extractor import WindowsCursorSkillsExtractor
            return WindowsCursorSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCursorSkillsExtractor
            return LinuxCursorSkillsExtractor()
        else:
            return None


class ClineSkillsExtractorFactory:
    """Factory for creating OS-specific Cline skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseClineSkillsExtractor]:
        """
        Create appropriate Cline skills extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseClineSkillsExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.cline.skills_extractor import MacOSClineSkillsExtractor
            return MacOSClineSkillsExtractor()
        elif os_name == "Windows":
            from .windows.cline.skills_extractor import WindowsClineSkillsExtractor
            return WindowsClineSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClineSkillsExtractor
            return LinuxClineSkillsExtractor()
        else:
            return None


class CodexSkillsExtractorFactory:
    """Factory for creating OS-specific OpenAI Codex skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseCodexSkillsExtractor]:
        """Create appropriate Codex skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.codex.skills_extractor import MacOSCodexSkillsExtractor
            return MacOSCodexSkillsExtractor()
        elif os_name == "Windows":
            from .windows.codex.skills_extractor import WindowsCodexSkillsExtractor
            return WindowsCodexSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxCodexSkillsExtractor
            return LinuxCodexSkillsExtractor()
        else:
            return None


class GeminiCliSkillsExtractorFactory:
    """Factory for creating OS-specific Gemini CLI skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseGeminiCliSkillsExtractor]:
        """Create appropriate Gemini CLI skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.gemini_cli.skills_extractor import MacOSGeminiCliSkillsExtractor
            return MacOSGeminiCliSkillsExtractor()
        elif os_name == "Windows":
            from .windows.gemini_cli.skills_extractor import WindowsGeminiCliSkillsExtractor
            return WindowsGeminiCliSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxGeminiCliSkillsExtractor
            return LinuxGeminiCliSkillsExtractor()
        else:
            return None


class JunieSkillsExtractorFactory:
    """Factory for creating OS-specific Junie skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseJunieSkillsExtractor]:
        """Create appropriate Junie skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.junie.skills_extractor import MacOSJunieSkillsExtractor
            return MacOSJunieSkillsExtractor()
        elif os_name == "Windows":
            from .windows.junie.skills_extractor import WindowsJunieSkillsExtractor
            return WindowsJunieSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxJunieSkillsExtractor
            return LinuxJunieSkillsExtractor()
        else:
            return None


class KiloCodeSkillsExtractorFactory:
    """Factory for creating OS-specific Kilo Code skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseKiloCodeSkillsExtractor]:
        """Create appropriate Kilo Code skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.kilocode.skills_extractor import MacOSKiloCodeSkillsExtractor
            return MacOSKiloCodeSkillsExtractor()
        elif os_name == "Windows":
            from .windows.kilocode.skills_extractor import WindowsKiloCodeSkillsExtractor
            return WindowsKiloCodeSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxKiloCodeSkillsExtractor
            return LinuxKiloCodeSkillsExtractor()
        else:
            return None


class OpenCodeSkillsExtractorFactory:
    """Factory for creating OS-specific OpenCode skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseOpenCodeSkillsExtractor]:
        """Create appropriate OpenCode skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.opencode.skills_extractor import MacOSOpenCodeSkillsExtractor
            return MacOSOpenCodeSkillsExtractor()
        elif os_name == "Windows":
            from .windows.opencode.skills_extractor import WindowsOpenCodeSkillsExtractor
            return WindowsOpenCodeSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxOpenCodeSkillsExtractor
            return LinuxOpenCodeSkillsExtractor()
        else:
            return None


class RooSkillsExtractorFactory:
    """Factory for creating OS-specific Roo Code skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseRooSkillsExtractor]:
        """Create appropriate Roo Code skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.roo_code.skills_extractor import MacOSRooSkillsExtractor
            return MacOSRooSkillsExtractor()
        elif os_name == "Windows":
            from .windows.roo_code.skills_extractor import WindowsRooSkillsExtractor
            return WindowsRooSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxRooSkillsExtractor
            return LinuxRooSkillsExtractor()
        else:
            return None


class ReplitSkillsExtractorFactory:
    """Factory for creating OS-specific Replit skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseReplitSkillsExtractor]:
        """Create appropriate Replit skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.replit.skills_extractor import MacOSReplitSkillsExtractor
            return MacOSReplitSkillsExtractor()
        elif os_name == "Windows":
            from .windows.replit.skills_extractor import WindowsReplitSkillsExtractor
            return WindowsReplitSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxReplitSkillsExtractor
            return LinuxReplitSkillsExtractor()
        else:
            return None


class WindsurfSkillsExtractorFactory:
    """Factory for creating OS-specific Windsurf skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseWindsurfSkillsExtractor]:
        """Create appropriate Windsurf skills extractor for the OS (None if unsupported)."""
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos.windsurf.skills_extractor import MacOSWindsurfSkillsExtractor
            return MacOSWindsurfSkillsExtractor()
        elif os_name == "Windows":
            from .windows.windsurf.skills_extractor import WindowsWindsurfSkillsExtractor
            return WindowsWindsurfSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxWindsurfSkillsExtractor
            return LinuxWindsurfSkillsExtractor()
        else:
            return None


class ClaudeCoworkSkillsExtractorFactory:
    """Factory for creating OS-specific Claude Cowork skills extractors."""

    @staticmethod
    def create(os_name: Optional[str] = None) -> Optional[BaseClaudeCoworkSkillsExtractor]:
        """
        Create appropriate Claude Cowork skills extractor for the OS.

        Args:
            os_name: Operating system name (defaults to current OS)

        Returns:
            BaseClaudeCoworkSkillsExtractor instance or None if OS is not supported
        """
        if os_name is None:
            os_name = platform.system()

        if os_name == "Darwin":
            from .macos import MacOSClaudeCoworkSkillsExtractor
            return MacOSClaudeCoworkSkillsExtractor()
        elif os_name == "Windows":
            from .windows import WindowsClaudeCoworkSkillsExtractor
            return WindowsClaudeCoworkSkillsExtractor()
        elif os_name == "Linux":
            from .linux import LinuxClaudeCoworkSkillsExtractor
            return LinuxClaudeCoworkSkillsExtractor()
        else:
            return None
