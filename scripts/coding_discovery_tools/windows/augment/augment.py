"""
Augment Code detection for Windows.

Augment Code keeps its config under ``%USERPROFILE%\\.augment`` (i.e.
``~/.augment``), identical to the macOS layout. This subclass inherits the full
macOS detection surface (per-surface CLI/VS Code/JetBrains rows) and overrides
only the OS-specific seams: the all-users (``C:\\Users``) scan, the Windows
``auggie`` binary resolve (npm ``.cmd`` shim, WinGet links, ``.local``/``.bun``),
and the Windows JetBrains detector. The version probe is inherited unchanged
(``run_command``; no ``shell=True``), per the plan.
"""

import logging
from pathlib import Path
from typing import List, Optional

from ...windows.jetbrains.jetbrains import WindowsJetBrainsDetector
from ...windows_extraction_helpers import is_running_as_admin
from ...macos.augment.augment import MacOSAugmentDetector

logger = logging.getLogger(__name__)


class WindowsAugmentDetector(MacOSAugmentDetector):
    """Detector for Augment Code surfaces on Windows systems."""

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: this user (scoped), else C:\\Users (admin), else home."""
        if self.user_home is not None:
            return [self.user_home]
        if is_running_as_admin():
            homes: List[Path] = []
            users_dir = Path("C:\\Users")
            try:
                if users_dir.exists():
                    for user_dir in users_dir.iterdir():
                        if user_dir.is_dir() and not user_dir.name.startswith("."):
                            homes.append(user_dir)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Error scanning C:\\Users for Augment: {exc}")
            return homes
        return [Path.home()]

    def _resolve_binary(self, user_home: Path) -> Optional[str]:
        """Resolve the Windows ``auggie`` binary (npm ``.cmd`` shim / WinGet / etc.)."""
        try:
            for candidate in (
                user_home / "AppData" / "Roaming" / "npm" / "auggie.cmd",
                user_home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "auggie.exe",
                user_home / ".local" / "bin" / "auggie.exe",
                user_home / ".bun" / "bin" / "auggie.exe",
            ):
                try:
                    if candidate.exists():
                        return str(candidate)
                except OSError:
                    continue
        except (PermissionError, OSError) as exc:
            logger.debug(f"Error resolving Auggie CLI binary for {user_home}: {exc}")
        return None

    def _make_jetbrains_detector(self):
        """OS seam: the Windows JetBrains detector."""
        return WindowsJetBrainsDetector()
