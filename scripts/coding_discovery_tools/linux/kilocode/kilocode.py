"""Kilo Code detection for Linux.

Detection gates on whether the Kilo Code extension is a LIVE entry in each
editor's ``extensions.json`` install registry (VS Code rewrites this file on
uninstall). The extension's ``globalStorage/<ext-id>`` directory is NOT used: VS
Code does not clean it up on uninstall (microsoft/vscode#119022), so gating on it
surfaced phantom rows for removed extensions. Linux previously had NO host-editor
gate at all, so it gains the live-entry gate here.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

logger = logging.getLogger(__name__)


class LinuxKiloCodeDetector(BaseToolDetector):
    """Detector for Kilo Code installations on Linux systems."""

    SUPPORTED_IDES = ["Code", "Cursor"]
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"

    @property
    def tool_name(self) -> str:
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            result = self._check_user_for_kilocode(user_home)
            if result:
                return result
        return None

    def get_version(self) -> Optional[str]:
        """
        Delegate to detect() so the install-gating logic (the extension being a
        live ``extensions.json`` entry) stays the single source of truth — a
        removed extension whose residue lingers must not surface a version when
        detect() returns None.
        """
        result = self.detect()
        if result:
            version = result.get("version")
            return version if version != "Unknown" else None
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        for ide_name in self.SUPPORTED_IDES:
            extension_info = self._check_kilocode_extension(user_home, ide_name)
            if not extension_info:
                continue
            _, version = extension_info
            return {
                "name": self.tool_name,
                "version": version or "Unknown",
                "install_path": str(extensions_dir_for_editor(user_home, ide_name)),
            }
        return None

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[str, Optional[str]]]:
        """Return ``(matched_location, version)`` if Kilo Code is a live entry in
        the editor's ``extensions.json``, else None."""
        return find_extension_in_editor(user_home, ide_name, self.KILOCODE_EXTENSION_ID)
