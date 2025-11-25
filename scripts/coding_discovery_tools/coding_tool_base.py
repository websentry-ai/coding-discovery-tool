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


class BaseClineRulesExtractor(ABC):
    """Abstract base class for extracting Cline rules from all projects."""

    @abstractmethod
    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on the machine.
        
        According to Cline documentation (https://docs.cline.bot/features/cline-rules):
        
        Global Rules:
        - Windows: Documents\Cline\Rules (uses system Documents folder)
        - macOS: ~/Documents/Cline/Rules
        - Linux/WSL: ~/Documents/Cline/Rules (may fall back to ~/Cline/Rules)
        - All markdown files (*.md) in the Rules directory are processed
        
        Workspace Rules:
        - .clinerules/ directory: All .md files inside are processed (folder system)
        - .clinerules file: Single file in project root
        - AGENTS.md: Fallback support for AGENTS.md standard
        
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

