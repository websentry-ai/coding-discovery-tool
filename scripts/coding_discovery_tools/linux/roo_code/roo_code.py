"""Roo Code detection for Linux.

Detection gates on whether the Roo extension is a LIVE entry in each editor's
``extensions.json`` install registry (VS Code rewrites this file on uninstall).
The extension's ``globalStorage/<ext-id>`` directory is NOT used: VS Code does
not clean it up on uninstall (microsoft/vscode#119022), so gating on it surfaced
phantom rows for removed extensions. The host-editor install AND-gate is likewise
dropped — the ``extensions.json`` entry is itself proof of a live install.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

logger = logging.getLogger(__name__)


class LinuxRooDetector(BaseToolDetector):
    """Detector for Roo Code installations on Linux systems."""

    SUPPORTED_IDES = {
        "Code": "VS Code",
        "Cursor": "Cursor",
        "Windsurf": "Windsurf",
        "VSCodium": "VSCodium",
    }

    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"

    @property
    def tool_name(self) -> str:
        return "Roo Code"

    def detect(self) -> Optional[List[Dict]]:
        all_results = []
        for user_home in get_linux_user_homes():
            try:
                all_results.extend(self._detect_roo_for_user(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_home}: {e}")
        return all_results if all_results else None

    def get_version(self) -> Optional[str]:
        result = self.detect()
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get("version", "Unknown")
        return None

    def _detect_roo_for_user(self, user_home: Path) -> List[Dict]:
        results = []
        # Gate on the extensions.json entry alone — no host-install AND-gate, since
        # the entry is itself proof of a live install.
        for ide_folder, ide_display_name in self.SUPPORTED_IDES.items():
            extension_info = self._check_roo_extension(user_home, ide_folder)
            if extension_info:
                _, version = extension_info
                results.append({
                    "name": f"Roo Code ({ide_display_name})",
                    "version": version or "Unknown",
                    "publisher": "Roo Veterinary Inc",
                    "ide": ide_display_name,
                    "install_path": str(extensions_dir_for_editor(user_home, ide_folder)),
                })
                logger.info(f"Detected: Roo Code ({ide_display_name}) v{version or 'Unknown'}")
        return results

    def _check_roo_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[str, Optional[str]]]:
        """Return ``(matched_location, version)`` if Roo Code is a live entry in
        the editor's ``extensions.json``, else None."""
        return find_extension_in_editor(user_home, ide_name, self.ROO_EXTENSION_ID)
