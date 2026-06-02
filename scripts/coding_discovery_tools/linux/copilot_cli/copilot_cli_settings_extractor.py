"""
GitHub Copilot CLI settings/permissions extraction for Linux.

Subclasses the macOS extractor, overriding only ``_scan_all_user_homes``
to use ``get_linux_user_homes()`` (``/root`` + ``/home/*``) instead of the
macOS ``scan_user_directories``. All permission extraction logic is inherited.
"""

import logging
from pathlib import Path

from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli_settings_extractor import MacOSCopilotCliSettingsExtractor

logger = logging.getLogger(__name__)


class LinuxCopilotCliSettingsExtractor(MacOSCopilotCliSettingsExtractor):
    """Extractor for GitHub Copilot CLI durable permissions on Linux."""

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every Linux user home."""
        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping {user_home}: {exc}")
