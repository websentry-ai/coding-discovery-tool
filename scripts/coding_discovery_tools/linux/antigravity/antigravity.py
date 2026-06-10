"""Antigravity detection for Linux."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


# Common system-wide install locations for Google's Antigravity (a VS Code
# fork distributed via .deb / .rpm / tarball). The flat installs ship a
# resources/app/product.json with a "version" key, matching the VS Code
# resource layout. The Antigravity 2.0 tarball installs to
# ``/opt/antigravity-ide`` and is ARCH-NESTED (e.g.
# ``/opt/antigravity-ide/Antigravity-IDE/Antigravity-x64/antigravity``), so a
# flat resources gate alone misses it — see ``_has_install_artifact``.
_SYSTEM_INSTALL_DIRS = (
    Path("/opt/Antigravity"),
    Path("/opt/antigravity"),
    Path("/opt/antigravity-ide"),
    Path("/opt/antigravity-ide/Antigravity-IDE"),
    Path("/usr/lib/antigravity"),
    Path("/usr/share/antigravity"),
)

# Per-user install locations (some users sideload tarballs into ~/.local).
_USER_INSTALL_RELATIVE_DIRS = (
    Path(".local/share/Antigravity"),
    Path(".local/share/antigravity"),
)

# Arch-nested / flat launcher executables under an Antigravity install dir
# (Antigravity 2.0 tarball). Presence of any of these qualifies the dir as a
# real install even when the flat ``resources/app`` tree is one level deeper.
_LAUNCHER_RELATIVE_PATHS = (
    Path("Antigravity-x64/antigravity"),
    Path("Antigravity-arm64/antigravity"),
    Path("antigravity-ide"),
    Path("antigravity"),
)


class LinuxAntigravityDetector(BaseToolDetector):
    """Antigravity detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Antigravity"

    def detect(self) -> Optional[Dict]:
        """
        Detect Antigravity by requiring a real install resource tree.

        Antigravity is a VS Code fork, so a genuine install ships a
        ``resources/app/product.json`` (or ``package.json``) under one of the
        conventional install dirs. The Antigravity 2.0 tarball is instead
        ARCH-NESTED (the launcher lives at ``<dir>/Antigravity-x64/antigravity``
        and friends), which the flat resources gate misses — so we ALSO accept
        a dir holding one of those launcher executables. We gate on those
        artifacts EXISTING — rather than on ``~/.antigravity`` (a residue
        config/data dir that survives uninstall) and rather than on a
        *parseable* ``version`` key. An install whose product/package json
        lacks a ``version`` is still a real install, so version parsing is
        decoupled: ``get_version()`` is called independently and a missing
        version is reported as ``"Unknown"`` (matching Replit-Linux / KiloCode).
        """
        for install_dir in self._candidate_install_dirs():
            try:
                if not install_dir.exists() or not install_dir.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            if self._has_install_artifact(install_dir):
                return {
                    "name": self.tool_name,
                    "version": self.get_version() or "Unknown",
                    "install_path": str(install_dir),
                }
        return None

    @staticmethod
    def _has_install_artifact(install_dir: Path) -> bool:
        """Return True iff ``install_dir`` holds a real Antigravity install
        artifact: a flat ``resources/app/product.json`` / ``package.json``
        resource tree (VS Code layout), OR an arch-nested / flat launcher
        executable (``Antigravity-x64/antigravity`` etc. — the 2.0 tarball).
        Never raises."""
        for filename in ("product.json", "package.json"):
            resource = install_dir / "resources" / "app" / filename
            try:
                if resource.exists() and resource.is_file():
                    return True
            except (PermissionError, OSError):
                continue
        for rel in _LAUNCHER_RELATIVE_PATHS:
            launcher = install_dir / rel
            try:
                if launcher.exists() and launcher.is_file():
                    return True
            except (PermissionError, OSError):
                continue
        return False

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
