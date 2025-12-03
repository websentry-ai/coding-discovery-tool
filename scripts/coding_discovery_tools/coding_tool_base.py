"""
Base classes for AI tools discovery system.

These abstract base classes define the interface for device ID extraction
and tool detection across different operating systems.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List


class BaseDeviceIdExtractor(ABC):
    """Abstract base class for device ID extraction."""

    @abstractmethod
    def extract_device_id(self) -> str:
        """
        Extract unique device identifier.
        
        Returns:
            Device serial number or hostname as fallback
        """
        pass


class BaseToolDetector(ABC):
    """Abstract base class for AI tool detection."""

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        pass

    @abstractmethod
    def detect(self) -> Optional[Dict]:
        """
        Detect if the tool is installed.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        pass

    @abstractmethod
    def get_version(self) -> Optional[str]:
        """
        Extract the version of the installed tool.
        
        Returns:
            Version string or None if version cannot be determined
        """
        pass


class BaseCursorRulesExtractor(ABC):
    """Abstract base class for extracting Cursor rules from all projects."""

    @abstractmethod
    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.
        
        Searches for:
        - User-level rules: ~/.cursor/*.mdc
        - Project-level rules: **/.cursor/*.mdc (recursive)
        - Legacy format: **/.cursorrules (recursive)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseClaudeRulesExtractor(ABC):
    """Abstract base class for extracting Claude Code rules from all projects."""

    @abstractmethod
    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.
        
        Searches for:
        - Current format: **/.clauderules (recursive)
        - Current format: **/.claude/.clauderules (recursive)
        - Legacy format: **/claude.md (recursive)
        - Legacy format: **/.claude/claude.md (recursive)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseWindsurfRulesExtractor(ABC):
    """Abstract base class for extracting Windsurf rules from all projects."""

    @abstractmethod
    def extract_all_windsurf_rules(self) -> List[Dict]:
        """
        Extract all Windsurf rules from all projects on the machine.
        
        Searches for:
        - Workspace-level rules: **/.windsurf/rules/** (recursive)
        - Global rules: ~/.windsurf/global_rules.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseClineRulesExtractor(ABC):
    """Abstract base class for extracting Cline rules from all projects."""

    @abstractmethod
    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on the machine.
        
        Searches for:
        - Workspace-level rules: **/.clinerules/*.md (recursive)
        - Global rules: ~/Documents/Cline/Rules/*.md or ~/Cline/Rules/*.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseAntigravityRulesExtractor(ABC):
    """Abstract base class for extracting Antigravity rules from all projects."""

    @abstractmethod
    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects on the machine.
        
        Searches for:
        - Project-level rules: **/.agent/rules/*.md (recursive)
        - Global rules: ~/.gemini/GEMINI.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseKiloCodeRulesExtractor(ABC):
    """Abstract base class for extracting Kilo Code rules from all projects."""

    @abstractmethod
    def extract_all_kilocode_rules(self) -> List[Dict]:
        """
        Extract all Kilo Code rules from all projects on the machine.
        
        Searches for:
        - Project-level rules: **/.kilocode/rules/*.md (recursive)
        - Global rules: ~/.kilocode/rules/*.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseGeminiCliRulesExtractor(ABC):
    """Abstract base class for extracting Gemini CLI rules from all projects."""

    @abstractmethod
    def extract_all_gemini_cli_rules(self) -> List[Dict]:
        """
        Extract all Gemini CLI rules from all projects on the machine.
        
        Searches for:
        - Global context: ~/.gemini/GEMINI.md
        - Project context: GEMINI.md in current working directory or any parent directory
        - Sub-directory context: GEMINI.md files in subdirectories
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseCodexRulesExtractor(ABC):
    """Abstract base class for extracting Codex rules from all projects."""

    @abstractmethod
    def extract_all_codex_rules(self) -> List[Dict]:
        """
        Extract all Codex rules from all projects on the machine.
        
        Searches for:
        - Global config: ~/.codex/config.toml (contains rules/execpolicy configuration)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseMCPConfigExtractor(ABC):
    """Abstract base class for extracting MCP configuration."""

    @abstractmethod
    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration for the tool.
        
        Returns:
            Dict with MCP config info (file_path, file_name, content, size,
            last_modified) or None if not found
        """
        pass

