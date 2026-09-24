"""Windsurf IDE detection for Linux."""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)

# The rebrand renamed the binary (``applicationName: devin-desktop``, dir ``Devin``);
# both ship in the wild. Not plain "devin" -- that is the agent CLI Devin bundles.
_BINARY_NAMES = ("windsurf", "devin-desktop")
_SYSTEM_PATHS = [
    Path("/usr/bin/windsurf"),
    Path("/usr/local/bin/windsurf"),
    Path("/opt/windsurf/windsurf"),
    Path("/usr/bin/devin-desktop"),
    Path("/usr/local/bin/devin-desktop"),
    Path("/opt/Devin/devin-desktop"),
]
_USER_RELATIVE_PATHS = [
    Path(".local/bin/windsurf"),
    Path(".local/share/windsurf/windsurf"),
    Path(".local/bin/devin-desktop"),
    Path(".local/share/Devin/devin-desktop"),
]


class LinuxWindsurfDetector(BaseToolDetector):
    """Windsurf IDE detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Devin Desktop"

    def detect(self) -> Optional[Dict]:
        for name in _BINARY_NAMES:
            which_out = run_command(["which", name], VERSION_TIMEOUT)
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

        # NOTE: do NOT fall back to ``~/.windsurf`` existence — that config dir
        # (~475 MB) survives uninstall, so it would report a phantom Windsurf
        # after the IDE is gone. The macOS/Windows detectors gate on the
        # app/binary only; match them.
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
        for name in _BINARY_NAMES:
            try:
                out = run_command([name, "--version"], VERSION_TIMEOUT)
                if out:
                    return extract_version_number(out)
            except Exception:
                continue
        return None
