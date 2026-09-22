"""
pi coding agent detection for Linux.

The agent uses the SAME per-user layout on Linux as on macOS (``~/.pi/agent``,
``~/.local/bin/pi``, nvm/bun prefixes), so the whole detection surface —
including the ``pi``-binary-name collision guard — is inherited. Only the
all-users enumeration differs: ``/home/*`` + ``/root`` instead of ``/Users/*``.
"""

from pathlib import Path
from typing import List

from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.pi.pi import MacOSPiDetector


class LinuxPiDetector(MacOSPiDetector):
    """Detector for the pi coding agent on Linux systems."""

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: the scoped one, else every Linux user home.

        ``get_linux_user_homes`` returns all human users when running as root
        (including ``/root``), else just the current user's home.
        """
        if self.user_home is not None:
            return [self.user_home]
        return [Path(home) for home in get_linux_user_homes()]
