"""Claude Code detection for Linux."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...linux_extraction_helpers import scan_user_directories, get_linux_user_homes
from ...user_tool_detector import find_claude_binary_for_user

logger = logging.getLogger(__name__)

# Common Claude Code install locations on Linux (checked in order)
_SYSTEM_PATHS = [
    Path("/usr/local/bin/claude"),
    Path("/usr/bin/claude"),
]
_USER_RELATIVE_PATHS = [
    Path(".local/bin/claude"),
    Path(".bun/bin/claude"),
    Path(".npm/bin/claude"),
    Path("go/bin/claude"),
]


class LinuxClaudeDetector(BaseToolDetector):
    """Claude Code detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Claude Code"

    def detect(self) -> Optional[Dict]:
        """Gates on the claude binary, not the ``~/.claude`` config directory, which
        survives uninstall (residue) and is created by other Claude surfaces.
        ``~/.claude`` remains the rules/MCP/skills source once detected here."""
        claude_bin = scan_user_directories(find_claude_binary_for_user) \
            or find_claude_binary_for_user(Path.home())
        if not claude_bin:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(),
            "install_path": str(claude_bin),
        }

    def get_version(self) -> Optional[str]:
        # System-wide paths first
        for binary in _SYSTEM_PATHS:
            try:
                if binary.exists():
                    out = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                    if out:
                        return extract_version_number(out)
            except Exception:
                continue

        # Per-user paths
        for user_home in get_linux_user_homes():
            for rel in _USER_RELATIVE_PATHS:
                binary = user_home / rel
                try:
                    if binary.exists():
                        out = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                        if out:
                            return extract_version_number(out)
                except Exception:
                    continue

        # PATH fallback
        try:
            out = run_command(["claude", "--version"], VERSION_TIMEOUT)
            return extract_version_number(out) if out else None
        except Exception:
            pass
        return None

    def extract_all_claude_rules(self) -> List[Dict]:
        from .claude_rules_extractor import LinuxClaudeRulesExtractor
        return LinuxClaudeRulesExtractor().extract_all_claude_rules()
