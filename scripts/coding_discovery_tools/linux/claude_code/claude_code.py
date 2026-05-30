"""Claude Code detection for Linux."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...linux_extraction_helpers import scan_user_directories, get_linux_user_homes

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
        # 1. Fastest: check PATH via `which`
        result = self._check_in_path()
        if result:
            return result

        # 2. When running as root, check every user home
        user_home = scan_user_directories(
            lambda d: d / ".claude" if (d / ".claude").exists() else None
        )
        if user_home:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(user_home),
            }

        # 3. Current user's ~/.claude
        claude_dir = Path.home() / ".claude"
        if claude_dir.exists():
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(claude_dir),
            }

        return None

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

    def _check_in_path(self) -> Optional[Dict]:
        out = run_command(["which", "claude"], VERSION_TIMEOUT)
        if out:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": out.strip(),
            }
        return None

    def extract_all_claude_rules(self) -> List[Dict]:
        from .claude_rules_extractor import LinuxClaudeRulesExtractor
        return LinuxClaudeRulesExtractor().extract_all_claude_rules()
