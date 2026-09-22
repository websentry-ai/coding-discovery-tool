"""
Zed detection for macOS.

Zed ships as a signed app bundle in ``/Applications`` (or ``~/Applications`` for
a non-admin install), with Preview/Nightly/Dev channels installed side by side
under their own bundle names. All channels report under ONE canonical ``Zed``
row — a per-channel row would split one user's install across several tool
entries downstream.

The ``/usr/local/bin/zed`` CLI shim is deliberately NOT a detection signal on
macOS: it is an optional symlink the user installs from inside the app, it
survives an app drag-to-Trash, and it would produce a second "Zed CLI" row for
the same install. The bundle is the signal.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import macos_app_candidates, read_bundle_version

logger = logging.getLogger(__name__)


class MacOSZedDetector(BaseToolDetector):
    """Detector for Zed on macOS systems."""

    # Ordered: the first channel that exists wins, so a stable install is
    # reported in preference to a Preview/Nightly sitting beside it. Exposed as
    # a class attribute so the order is visible to (and patchable by) tests.
    APP_PATHS = [
        Path("/Applications/Zed.app"),
        Path("/Applications/Zed Preview.app"),
        Path("/Applications/Zed Nightly.app"),
        Path("/Applications/Zed Dev.app"),
    ]

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Zed"

    def detect(self) -> Optional[Dict]:
        """
        Detect a Zed installation on macOS.

        Returns:
            Dict with name/version/install_path, or None when no bundle exists.
        """
        app_path = self._resolve_app_path()
        if app_path is None:
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version(app_path) or "Unknown",
            "install_path": str(app_path),
        }

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """Read ``CFBundleShortVersionString`` from the bundle's Info.plist.

        Uses ``read_bundle_version`` (O_NOFOLLOW + regular-file check) rather
        than ``defaults read``: the bundle can live under a user-writable home
        during a root scan, where a symlinked plist pointing at a FIFO would
        otherwise block the whole scan.
        """
        try:
            if app_path is None:
                app_path = self._resolve_app_path()
            if app_path is None:
                return None
            return read_bundle_version(app_path)
        except Exception as exc:
            logger.debug(f"Could not extract Zed version: {exc}")
        return None

    def _resolve_app_path(self) -> Optional[Path]:
        """The installed bundle: machine-wide, else the scanned user's own."""
        user_home = getattr(self, "user_home", None)
        for app_path in self.APP_PATHS:
            for candidate in macos_app_candidates(app_path, user_home):
                try:
                    if candidate.exists():
                        return candidate
                except (PermissionError, OSError):
                    continue
        return None

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: the scoped one, else the current user's home."""
        if self.user_home is not None:
            return [self.user_home]
        return [Path.home()]
