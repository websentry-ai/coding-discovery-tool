"""OpenCode detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)

_USER_RELATIVE_PATHS = [
    Path(".opencode/bin/opencode"),  # curl installer default
    Path(".bun/bin/opencode"),
    Path(".local/bin/opencode"),
    Path(".npm-global/bin/opencode"),
]


class LinuxOpenCodeDetector(BaseToolDetector):
    """Detector for OpenCode installations on Linux systems."""

    def __init__(self) -> None:
        # Set by the per-user dispatcher under a root scan; None = all homes.
        self.user_home: Optional[Path] = None
        self._resolved_path: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        return "OpenCode"

    def _iter_scan_homes(self):
        if self.user_home is not None:
            return [self.user_home]
        return get_linux_user_homes()

    def detect(self) -> Optional[Dict]:
        install_path = self._check_opencode_command()
        if not install_path:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def get_version(self) -> Optional[str]:
        try:
            # A per-user hit is not on PATH; probe the resolved binary itself.
            argv0 = str(self._resolved_path) if self._resolved_path else "opencode"
            output = run_command([argv0, "--version"], VERSION_TIMEOUT)
            if output:
                version = output.strip()
                return version or None
        except Exception as e:
            logger.debug(f"Could not extract OpenCode version: {e}")
        return None

    def _check_opencode_command(self) -> Optional[str]:
        self._resolved_path = None
        try:
            output = run_command(["which", "opencode"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found OpenCode at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for OpenCode command: {e}")
        for user_home in self._iter_scan_homes():
            for rel in _USER_RELATIVE_PATHS:
                p = user_home / rel
                try:
                    if p.is_file():
                        logger.debug(f"Found OpenCode at: {p}")
                        self._resolved_path = p
                        return str(p)
                except OSError:
                    continue
        return None
