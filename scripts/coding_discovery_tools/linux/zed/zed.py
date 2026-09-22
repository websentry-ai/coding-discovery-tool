"""
Zed detection for Linux.

A standalone detector, NOT a subclass of the macOS one: the install layouts
share nothing. Linux Zed lands as a self-contained tree under
``~/.local/zed.app`` (the official install script), a ``~/.local/bin/zed``
launcher, a flatpak under ``~/.var/app/dev.zed.Zed``, or a distro package whose
binary is sometimes named ``zeditor`` (Fedora/Arch rename it to avoid a clash
with the ``zed`` from the ZFS Event Daemon).

All layouts report under the single canonical ``Zed`` name.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...linux_extraction_helpers import get_linux_user_homes
from ...utils import extract_version_number, run_command

logger = logging.getLogger(__name__)


class LinuxZedDetector(BaseToolDetector):
    """Detector for Zed on Linux systems."""

    # Per-user candidates, in resolution order (first hit wins).
    USER_APP_DIRS = (
        Path(".local") / "zed.app",
        Path(".local") / "zed-preview.app",
    )
    USER_BIN_PATHS = (
        Path(".local") / "bin" / "zed",
        Path(".local") / "bin" / "zeditor",
    )
    USER_FLATPAK_DIRS = (
        Path(".var") / "app" / "dev.zed.Zed",
        Path(".var") / "app" / "dev.zed.Zed-Preview",
    )
    # Machine-wide distro packages. Exposed as a class attribute so tests can
    # isolate themselves from whatever the CI box happens to have installed.
    MACHINE_BIN_PATHS = [
        Path("/usr/bin/zed"),
        Path("/usr/bin/zeditor"),
        Path("/usr/local/bin/zed"),
        Path("/usr/local/bin/zeditor"),
    ]
    # Probed inside a resolved app dir, in order, for the version.
    APP_DIR_BIN_RELATIVE = (
        Path("bin") / "zed",
        Path("libexec") / "zed-editor",
    )

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Zed"

    def detect(self) -> Optional[Dict]:
        """
        Detect a Zed installation on Linux.

        Returns:
            Dict with name/version/install_path, or None when nothing is found.
        """
        install_path = self._resolve_install_path()
        if install_path is None:
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version(install_path) or "Unknown",
            "install_path": str(install_path),
        }

    def get_version(self, install_path: Optional[Path] = None) -> Optional[str]:
        """Probe ``<binary> --version`` for the resolved install.

        When the resolved hit is an app *directory* the bundled launcher is
        probed instead; a flatpak dir has neither, and reports no version rather
        than shelling out to ``flatpak run`` (which would start the editor).
        """
        try:
            if install_path is None:
                install_path = self._resolve_install_path()
            if install_path is None:
                return None
            binary = self._version_binary(install_path)
            if binary is None:
                return None
            output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
            if not output:
                return None
            return extract_version_number(output) or output.strip() or None
        except Exception as exc:
            logger.debug(f"Could not extract Zed version: {exc}")
        return None

    # -- internals ----------------------------------------------------------

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: the scoped one, else every Linux user home."""
        if self.user_home is not None:
            return [self.user_home]
        return [Path(home) for home in get_linux_user_homes()]

    def _version_binary(self, install_path: Path) -> Optional[Path]:
        """The executable to probe for ``install_path`` (None when there is none)."""
        try:
            if not install_path.is_dir():
                return install_path
            for relative in self.APP_DIR_BIN_RELATIVE:
                candidate = install_path / relative
                try:
                    if candidate.exists() and os.access(str(candidate), os.X_OK):
                        return candidate
                except OSError:
                    continue
        except OSError:
            return None
        return None

    def _resolve_install_path(self) -> Optional[Path]:
        """First matching install location. Never raises."""
        for user_home in self._iter_scan_homes():
            hit = self._resolve_for_user(user_home)
            if hit is not None:
                return hit
        for candidate in self.MACHINE_BIN_PATHS:
            try:
                if candidate.exists() and os.access(str(candidate), os.X_OK):
                    return candidate
            except OSError:
                continue
        return None

    def _resolve_for_user(self, user_home: Path) -> Optional[Path]:
        """Per-user install locations for ``user_home``, in order."""
        dir_and_bin: Tuple[Tuple[Path, bool], ...] = tuple(
            [(user_home / rel, True) for rel in self.USER_APP_DIRS]
            + [(user_home / rel, False) for rel in self.USER_BIN_PATHS]
            + [(user_home / rel, True) for rel in self.USER_FLATPAK_DIRS]
        )
        for candidate, expect_dir in dir_and_bin:
            try:
                if expect_dir:
                    if candidate.is_dir():
                        return candidate
                elif candidate.exists() and os.access(str(candidate), os.X_OK):
                    return candidate
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping Zed candidate {candidate}: {exc}")
                continue
        return None
