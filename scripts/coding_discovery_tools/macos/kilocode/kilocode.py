"""
Kilo Code detection for macOS.

Kilo Code is an AI-powered coding assistant that operates as a VS Code extension.
Detection gates on a LIVE entry in each editor's ``extensions.json`` registry, not
the ``globalStorage/<ext-id>`` dir, which survives uninstall (microsoft/vscode
#119022) and so produced phantom rows for removed extensions.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

logger = logging.getLogger(__name__)


class MacOSKiloCodeDetector(BaseToolDetector):
    """
    Detector for Kilo Code installations on macOS systems.

    Kilo Code operates as a VS Code extension, so detection gates on the
    extension being a live entry in the editor's ``extensions.json`` registry.
    """

    # Supported IDEs that can host the Kilo Code extension
    SUPPORTED_IDES = ['Code', 'Cursor']

    # Kilo Code extension identifier
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"

    # Application names for each IDE
    IDE_APP_NAMES = {
        "Code": ["Visual Studio Code.app"],
        "Cursor": ["Cursor.app"],
    }

    # Standard macOS applications directory
    APPLICATIONS_DIR = Path("/Applications")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Kilo Code installation on macOS.
        
        When running as root, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as root, scan all user directories first
        if is_running_as_root():
            user_kilocode_info = scan_user_directories(
                lambda user_dir: self._check_user_for_kilocode(user_dir)
            )
            if user_kilocode_info:
                return user_kilocode_info
        
        # Check current user (works for both root and regular users)
        return self._check_user_for_kilocode(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Kilo Code version.

        Delegates to detect() so the live-entry gate stays the single source of
        truth — leftover residue must not surface a version when detect() is None.

        Returns:
            Version string if KiloCode is installed, None otherwise.
        """
        result = self.detect()
        if result:
            version = result.get("version")
            return version if version != "Unknown" else None
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Kilo Code is installed for a specific user.

        Accepts the first editor whose ``extensions.json`` lists Kilo Code as a live
        entry. ``find_extension_in_editor`` matches case-insensitively — the registry
        stores ``kilocode.kilo-code`` but ``KILOCODE_EXTENSION_ID`` is display-cased.
        """
        for ide_name in self.SUPPORTED_IDES:
            extension_info = self._check_kilocode_extension(user_home, ide_name)
            if not extension_info:
                continue
            _, version = extension_info
            logger.debug(f"Found Kilo Code in {ide_name} at: {extensions_dir_for_editor(user_home, ide_name)}")
            return {
                "name": self.tool_name,
                "version": version or "Unknown",
                "install_path": str(extensions_dir_for_editor(user_home, ide_name)),
            }
        logger.debug("No editor lists Kilo Code as a live extensions.json entry")
        return None

    def _check_ide_installation(self, ide_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a specific IDE is installed in /Applications.
        
        First checks if the IDE installation path exists before proceeding.
        
        Args:
            ide_name: Name of the IDE to check (Code or Cursor)
            
        Returns:
            Tuple of (is_installed: bool, install_path: Optional[str])
        """
        app_names = self.IDE_APP_NAMES.get(ide_name, [])
        
        for app_name in app_names:
            ide_path = self.APPLICATIONS_DIR / app_name
            
            # First check if the path exists
            try:
                if not ide_path.exists():
                    logger.debug(f"IDE path does not exist: {ide_path}")
                    continue
                
                # Verify it's a directory
                if ide_path.is_dir():
                    logger.debug(f"Found {ide_name} installation at: {ide_path}")
                    return True, str(ide_path)
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check IDE path {ide_path}: {e}")
                continue
        
        return False, None

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Check if Kilo Code is a live entry in the editor's ``extensions.json``
        and return its version.

        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check

        Returns:
            Tuple of (matched_location, version) if found, None otherwise
        """
        return find_extension_in_editor(user_home, ide_name, self.KILOCODE_EXTENSION_ID)

