"""
Augment Code detection for Linux.

Augment Code keeps its config under ``~/.augment/`` (identical to macOS). This
subclass inherits the full macOS detection surface (per-surface CLI/VS Code/
JetBrains rows) and overrides only the all-users scan (``get_linux_user_homes()``)
and the Linux JetBrains detector. The ``auggie`` binary resolve and the version
probe are inherited unchanged (the per-user ``.local``/``.bun``/nvm prefixes apply
on Linux too).
"""

from pathlib import Path
from typing import List

from ...linux.jetbrains.jetbrains import LinuxJetBrainsDetector
from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.augment.augment import MacOSAugmentDetector


class LinuxAugmentDetector(MacOSAugmentDetector):
    """Detector for Augment Code surfaces on Linux systems."""

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: this user (scoped), else every Linux user home.

        ``get_linux_user_homes`` returns all human users when running as root
        (including ``/root``), else just the current user's home.
        """
        if self.user_home is not None:
            return [self.user_home]
        return [Path(home) for home in get_linux_user_homes()]

    def _make_jetbrains_detector(self):
        """OS seam: the Linux JetBrains detector."""
        return LinuxJetBrainsDetector()
