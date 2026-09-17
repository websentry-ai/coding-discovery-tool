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
from ...user_tool_detector import find_claude_binary_for_user
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

        Gates on the claude binary, not the ``~/.claude`` config directory, which
        survives uninstall (residue) and is created by other Claude surfaces.
        ``~/.claude`` remains the rules/MCP/skills source once detected here.

        Returns:
            Dict with tool info or None if not found
        """
        claude_bin = scan_user_directories(find_claude_binary_for_user) \
            or find_claude_binary_for_user(Path.home())
        if not claude_bin:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(claude_bin),
            "install_path": str(claude_bin)
        }

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """Extract Claude Code version.

        Probes ``binary`` when detection already resolved one, so the reported
        version belongs to the install being reported. Otherwise tries
        user-specific binary paths, then bare 'claude' via PATH lookup.
        """
        if binary is not None:
            try:
                output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                return extract_version_number(output) if output else None
            except Exception as e:
                logger.debug(f"Could not extract Claude Code version from {binary}: {e}")
                return None

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

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.
        
        Returns:
            List of project dicts, each containing project_root and rules array
        """
        extractor = MacOSClaudeRulesExtractor()
        return extractor.extract_all_claude_rules()

