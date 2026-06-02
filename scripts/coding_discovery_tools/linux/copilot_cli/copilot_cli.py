"""
GitHub Copilot CLI detection for Linux.

Subclasses the macOS detector, overriding only the all-users branch: Linux
uses ``get_linux_user_homes()`` (``/root`` + ``/home/*``) instead of the macOS
``/Users`` scan. Everything else — the marker gate, per-user detection,
``detect``/``detect_all_tools``, and ``get_version`` — is inherited unchanged.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector

logger = logging.getLogger(__name__)


class LinuxCopilotCliDetector(MacOSCopilotCliDetector):
    """
    Detector for GitHub Copilot CLI installations on Linux systems.

    Inherits the full macOS detection surface and overrides only the
    all-users branch to use ``get_linux_user_homes()`` (``/root`` +
    ``/home/*``) instead of ``/Users``.
    """

    def _detect_all_users(self) -> List[Dict]:
        """
        Detect the Copilot CLI for the relevant set of users on Linux.

        - If ``self.user_home`` is set, check only that user (live per-user
          discovery path).
        - Otherwise scan every home returned by ``get_linux_user_homes()``
          (includes ``/root`` and all ``/home/*`` dirs).
        """
        if self.user_home is not None:
            result = self._detect_for_user(self.user_home)
            return [result] if result else []

        results: List[Dict] = []
        for user_home in get_linux_user_homes():
            try:
                result = self._detect_for_user(user_home)
                if result:
                    results.append(result)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping user directory {user_home}: {exc}")
        return results
