"""
Claude Code detection for macOS
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...macos_extraction_helpers import scan_user_directories
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

        # When running as root, scan user directories first
        user_claude_dir = scan_user_directories(
            lambda user_dir: user_dir / ".claude" if (user_dir / ".claude").exists() else None
        )
        if user_claude_dir:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(user_claude_dir)
            }
        
        # Check current user's home directory (works for both root and regular users)
        claude_dir = Path.home() / ".claude"
        if claude_dir.exists():
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(claude_dir)
            }

        return None

    def get_version(self) -> Optional[str]:
        """Extract Claude Code version.

        Tries user-specific binary paths first (does not rely on PATH),
        then falls back to bare 'claude' command via PATH lookup.
        """
        try:
            # Always try system-wide absolute paths first (works in daemon containers / MDM)
            system_paths = [
                Path("/opt/homebrew/bin/claude"),
                Path("/usr/local/bin/claude"),
            ]
            user_paths = []
            if hasattr(self, 'user_home') and self.user_home:
                user_home = Path(self.user_home) if not isinstance(self.user_home, Path) else self.user_home
                user_paths = [
                    user_home / ".local" / "bin" / "claude",
                    user_home / ".bun" / "bin" / "claude",
                ]
            for binary in system_paths + user_paths:
                try:
                    if binary.exists():
                        output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                        if output:
                            return extract_version_number(output)
                except Exception:
                    continue

            # Fallback to PATH-based lookup
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

