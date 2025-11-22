"""
Claude Code detection for Linux
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from .claude_rules_extractor import LinuxClaudeRulesExtractor

logger = logging.getLogger(__name__)


class LinuxClaudeDetector(BaseToolDetector):
    """Claude Code detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Claude Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Claude Code installation on Linux.

        Returns:
            Dict with tool info or None if not found
        """
        # Check PATH first
        claude_info = self._check_in_path()
        if claude_info:
            return claude_info

        # Check common installation locations
        install_paths = self._get_search_paths()
        for install_path in install_paths:
            if install_path.exists():
                claude_exe = install_path / "claude"
                if claude_exe.exists():
                    return {
                        "name": self.tool_name,
                        "version": self.get_version(),
                        "install_path": str(install_path)
                    }

        # Check .claude directory in home
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
                "install_path": output.strip()
            }
        return None

    def _get_search_paths(self) -> List[Path]:
        """
        Get list of paths to search for Claude Code installation.

        Returns:
            List of Path objects
        """
        user_home = Path.home()
        return [
            user_home / ".local" / "bin",
            user_home / "bin",
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/opt") / "claude",
            Path("/opt") / "claude-code",
            user_home / ".npm" / "bin",
            user_home / ".yarn" / "bin",
        ]

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.

        Returns:
            List of project dicts, each containing project_root and rules array
        """
        extractor = LinuxClaudeRulesExtractor()
        return extractor.extract_all_claude_rules()