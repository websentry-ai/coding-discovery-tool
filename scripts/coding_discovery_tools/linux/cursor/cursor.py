"""Cursor IDE detection for Linux."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)

# Common Cursor install locations on Linux
_SYSTEM_PATHS = [
    Path("/usr/bin/cursor"),
    Path("/usr/local/bin/cursor"),
    Path("/opt/cursor/cursor"),
]
_USER_RELATIVE_PATHS = [
    Path(".local/bin/cursor"),
    Path(".local/share/cursor/cursor"),
    Path(".local/share/Cursor/cursor"),
]


class LinuxCursorDetector(BaseToolDetector):
    """Cursor IDE detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Cursor"

    def detect(self) -> Optional[Dict]:
        # 1. PATH check
        which_out = run_command(["which", "cursor"], VERSION_TIMEOUT)
        if which_out:
            install_path = which_out.strip()
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": install_path,
            }

        # 2. System-wide paths
        for p in _SYSTEM_PATHS:
            if p.exists() and p.is_file():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(p),
                }

        # 3. Per-user binary paths.
        # NOTE: do NOT fall back to ``~/.cursor`` existence — that config dir
        # survives uninstall and is shared with Cursor CLI / rules tooling, so it
        # would report a phantom Cursor after the IDE is gone (WEB-4771). The
        # macOS/Windows detectors gate on the app/binary only; match them.
        for user_home in get_linux_user_homes():
            for rel in _USER_RELATIVE_PATHS:
                p = user_home / rel
                if p.exists() and p.is_file():
                    return {
                        "name": self.tool_name,
                        "version": self.get_version(),
                        "install_path": str(p),
                    }

        return None

    def get_version(self) -> Optional[str]:
        for binary in _SYSTEM_PATHS:
            try:
                if binary.exists():
                    out = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                    if out:
                        return extract_version_number(out)
            except Exception:
                continue

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

        try:
            out = run_command(["cursor", "--version"], VERSION_TIMEOUT)
            return extract_version_number(out) if out else None
        except Exception:
            pass
        return None

    def extract_all_cursor_rules(self) -> List[Dict]:
        from .cursor_rules_extractor import LinuxCursorRulesExtractor
        return LinuxCursorRulesExtractor().extract_all_cursor_rules()
