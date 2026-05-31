"""Antigravity detection for Linux."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


# Common system-wide install locations for Google's Antigravity (a VS Code
# fork distributed via .deb / .rpm / tarball). All ship a resources/app/
# product.json with a "version" key, matching the VS Code resource layout.
_SYSTEM_INSTALL_DIRS = (
    Path("/opt/Antigravity"),
    Path("/opt/antigravity"),
    Path("/usr/lib/antigravity"),
    Path("/usr/share/antigravity"),
)

# Per-user install locations (some users sideload tarballs into ~/.local).
_USER_INSTALL_RELATIVE_DIRS = (
    Path(".local/share/Antigravity"),
    Path(".local/share/antigravity"),
)


class LinuxAntigravityDetector(BaseToolDetector):
    """Antigravity detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Antigravity"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            antigravity_dir = user_home / ".antigravity"
            if antigravity_dir.exists() and antigravity_dir.is_dir():
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(antigravity_dir),
                }
        return None

    def get_version(self) -> Optional[str]:
        """
        Read Antigravity's version from any discoverable install directory.

        Antigravity is a VS Code fork, so ``resources/app/product.json`` and
        ``resources/app/package.json`` carry the version exactly the way our
        Cursor/Windsurf detectors expect. We do not require an IDE to be in
        the user's PATH — sideloaded tarballs and system installs are both
        valid sources.

        Returns:
            Version string from product.json/package.json, or None if no
            install can be found.
        """
        for install_dir in self._candidate_install_dirs():
            try:
                if not install_dir.exists() or not install_dir.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            for filename in ("product.json", "package.json"):
                version = self._read_version_file(install_dir / "resources" / "app" / filename)
                if version:
                    return version
        return None

    def _candidate_install_dirs(self) -> List[Path]:
        dirs: List[Path] = list(_SYSTEM_INSTALL_DIRS)
        for user_home in get_linux_user_homes():
            for rel in _USER_INSTALL_RELATIVE_DIRS:
                dirs.append(user_home / rel)
        return dirs

    def _read_version_file(self, path: Path) -> Optional[str]:
        try:
            if not path.exists() or not path.is_file():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("version")
        except (json.JSONDecodeError, OSError, PermissionError) as e:
            logger.debug(f"Could not read Antigravity version file {path}: {e}")
        return None
