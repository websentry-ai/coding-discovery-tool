"""
Replit detection for macOS.

Replit is an online IDE and coding platform.
This module detects Replit installations by checking for the application
bundle at /Applications/Replit.app. The bundle is removed on uninstall, so it
is a reliable "installed" signal (unlike the user data directory, which
survives uninstall and caused false positives).
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on macOS systems.

    Detection gates on the Replit.app bundle in /Applications, which is
    removed on uninstall.
    """

    # Application installation path
    APPLICATION_PATH = Path("/Applications/Replit.app")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit installation on macOS.

        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Gate on the installed .app bundle, which is removed on uninstall.
        # The former ``~/Library/Application Support/Replit/`` fallback was a
        # residue data dir that survives uninstall and produced false positives.
        # ``or "Unknown"`` keeps the version field consistent with KiloCode.
        app_path = self._check_application_installation()
        if app_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(app_path) or "Unknown",
                "install_path": str(app_path)
            }

        return None

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """
        Extract Replit version.

        Replit Desktop is a standard Electron app, so version lives in two
        well-known places: the .app's Info.plist (CFBundleShortVersionString,
        same source Cursor/Windsurf use) and resources/app/package.json. Try
        the plist first because ``defaults read`` is significantly cheaper
        than parsing the JSON resource bundle.

        Args:
            app_path: Optional already-resolved .app path. When ``detect()``
                has just confirmed the install, passing it here avoids a
                redundant ``_check_application_installation()`` filesystem
                call. Matches the macOS Antigravity detector's signature.

        Returns:
            Version string if the app is installed, None otherwise.
        """
        if app_path is None:
            app_path = self._check_application_installation()
        if not app_path:
            return None
        try:
            plist_path = app_path / "Contents" / "Info.plist"
            if plist_path.exists():
                output = run_command(
                    ["defaults", "read", str(plist_path), "CFBundleShortVersionString"],
                    VERSION_TIMEOUT,
                )
                if output:
                    return output.strip()
        except Exception as e:
            logger.debug(f"Could not read Replit Info.plist: {e}")
        try:
            pkg_json = app_path / "Contents" / "Resources" / "app" / "package.json"
            if pkg_json.exists():
                with open(pkg_json, "r", encoding="utf-8") as f:
                    return json.load(f).get("version")
        except (json.JSONDecodeError, OSError, PermissionError) as e:
            logger.debug(f"Could not read Replit package.json: {e}")
        return None

    def _check_application_installation(self) -> Optional[Path]:
        """
        Check if Replit application is installed in /Applications.
        
        Returns:
            Path to Replit.app if found, None otherwise
        """
        try:
            if self.APPLICATION_PATH.exists() and self.APPLICATION_PATH.is_dir():
                logger.debug(f"Found Replit application at: {self.APPLICATION_PATH}")
                return self.APPLICATION_PATH
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Replit application path: {e}")

        return None

