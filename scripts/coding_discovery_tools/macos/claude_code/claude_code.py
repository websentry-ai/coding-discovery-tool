"""
Claude Code detection for macOS
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from .claude_rules_extractor import MacOSClaudeRulesExtractor

logger = logging.getLogger(__name__)


class MacOSClaudeDetector(BaseToolDetector):
    """Claude Code detector for macOS systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Claude Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Claude Code installation on macOS.
        
        Returns:
            Dict with tool info or None if not found
        """
        # Check PATH first (works for both regular users and root)
        claude_info = self._check_in_path()
        if claude_info:
            return claude_info

        # When running as root, prioritize checking user directories first
        if Path.home() == Path("/root"):
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        claude_dir = user_dir / ".claude"
                        if claude_dir.exists():
                            return {
                                "name": self.tool_name,
                                "version": self.get_version(),
                                "install_path": str(claude_dir)
                            }
            # Fallback to root's .claude directory if no user installation found
            claude_dir = Path.home() / ".claude"
            if claude_dir.exists():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(claude_dir)
                }
        else:
            # For regular users, check their own home directory
            claude_dir = Path.home() / ".claude"
            if claude_dir.exists():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(claude_dir)
                }

        return None

    def get_version(self) -> Optional[str]:
        """Extract Claude Code version."""
        try:
            output = run_command(["claude", "--version"], VERSION_TIMEOUT)
            return extract_version_number(output) if output else None
        except Exception as e:
            logger.warning(f"Could not extract Claude Code version: {e}")
        return None

    def _check_in_path(self) -> Optional[Dict]:
        """Check if claude is in PATH."""
        output = run_command(["which", "claude"], VERSION_TIMEOUT)
        if output:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": output
            }
        return None

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.
        
        Returns:
            List of project dicts, each containing project_root and rules array
        """
        extractor = MacOSClaudeRulesExtractor()
        return extractor.extract_all_claude_rules()

