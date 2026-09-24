"""
Muse Code detection for macOS.

Muse Code is Meta's terminal coding agent. Its installer
(``curl -fsSL https://dev.meta.ai/install.sh | bash``) writes a bash launcher to
``~/.local/bin/muse`` beside the versioned binary it downloads, and records the
installed version in ``~/.local/bin/.muse-version``.

"muse" is a common binary name, so the launcher alone is not proof of install:
the installer's ``.muse-version`` marker must sit beside it. The version is read
from that marker, so nothing is executed.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import _read_own_regular_file

logger = logging.getLogger(__name__)

MUSE_CODE_INSTALL_DIR = Path(".local") / "bin"
_LAUNCHER_NAME = "muse"
_VERSION_MARKER_NAME = ".muse-version"
_MAX_VERSION_BYTES = 64
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.]+)?")


class MacOSMuseCodeDetector(BaseToolDetector):
    """Detector for Muse Code on macOS systems."""

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Muse Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect a Muse Code installation for the scanned user.

        Returns:
            Dict with name/version/install_path, or None when the installer's
            launcher and version marker are not both present.
        """
        home = self._scan_home()
        install_dir = home / MUSE_CODE_INSTALL_DIR
        launcher = install_dir / _LAUNCHER_NAME
        try:
            if not (launcher.is_file() and (install_dir / _VERSION_MARKER_NAME).is_file()):
                return None
        except OSError as e:
            logger.debug(f"Error checking Muse Code install: {e}")
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": str(launcher),
        }

    def get_version(self) -> Optional[str]:
        """Read the installed version from the installer's ``.muse-version`` marker.

        The marker is refused when multiply linked (a hard link to auth.json
        would otherwise ship token bytes as the "version"), and only text shaped
        like a version (``1.3.0-R3401.1``) is returned.
        """
        home = self._scan_home()
        marker = home / MUSE_CODE_INSTALL_DIR / _VERSION_MARKER_NAME
        try:
            if os.lstat(marker).st_nlink != 1:
                return None
        except OSError:
            return None
        content = _read_own_regular_file(marker, home, _MAX_VERSION_BYTES)
        version = content.strip() if content else ""
        return version if _VERSION_RE.fullmatch(version) else None

    def _scan_home(self) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(self.user_home or Path.home())
