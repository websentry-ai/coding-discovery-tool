"""Windsurf IDE detection for Linux."""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)

_SYSTEM_PATHS = [
    Path("/usr/bin/windsurf"),
    Path("/usr/local/bin/windsurf"),
    Path("/opt/windsurf/windsurf"),
]
_USER_RELATIVE_PATHS = [
    Path(".local/bin/windsurf"),
    Path(".local/share/windsurf/windsurf"),
]


class LinuxWindsurfDetector(BaseToolDetector):
    """Windsurf IDE detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Windsurf"

    def detect(self) -> Optional[Dict]:
        which_out = run_command(["which", "windsurf"], VERSION_TIMEOUT)
        if which_out:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": which_out.strip(),
            }

        for p in _SYSTEM_PATHS:
            if p.exists() and p.is_file():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(p),
                }

        for user_home in get_linux_user_homes():
            for rel in _USER_RELATIVE_PATHS:
                p = user_home / rel
                if p.exists() and p.is_file():
                    return {
                        "name": self.tool_name,
                        "version": self.get_version(),
                        "install_path": str(p),
                    }
            # Config directory presence
            windsurf_dir = user_home / ".windsurf"
            if windsurf_dir.exists():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(windsurf_dir),
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
            out = run_command(["windsurf", "--version"], VERSION_TIMEOUT)
            return extract_version_number(out) if out else None
        except Exception:
            return None
