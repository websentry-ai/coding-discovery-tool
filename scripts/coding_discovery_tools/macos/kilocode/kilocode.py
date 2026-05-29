"""
Kilo Code detection for macOS.

Kilo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Kilo Code installations by checking for:
1. IDE installations (VS Code, Cursor)
2. Kilo Code extension settings in IDE global storage directories
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)

# Match the trailing semver portion of a VS Code extension folder name,
# including pre-release suffixes like 1.2.3-pre.5 or 1.0.0-beta.1.
_VERSION_SUFFIX_RE = re.compile(r"-(\d+\.\d+\.\d+(?:[-+][\w.+-]+)?)$")


class MacOSKiloCodeDetector(BaseToolDetector):
    """
    Detector for Kilo Code installations on macOS systems.
    
    Kilo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor)
    - Verifying Kilo Code extension settings exist in IDE global storage
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

        Delegates to detect() so the install-gating logic (extension settings
        dir + IDE present in /Applications) stays the single source of truth.
        A leftover extension folder without a real install must not surface
        a version when detect() would report nothing.

        Returns:
            Version string if KiloCode is installed, None otherwise.
        """
        result = self.detect()
        if result:
            version = result.get("version")
            return version if version != "Unknown" else None
        return None

    def _get_extension_version_for_user(self, user_home: Path, ide_name: str) -> Optional[str]:
        """
        Read the Kilo Code extension version for a single IDE.

        Scoped to one IDE so the version always matches the install_path
        reported by detect() — looking in another IDE's extensions dir would
        risk returning a leftover VS Code version against a Cursor install.

        Reads ``package.json`` inside the matching extension folder, falling
        back to the version suffix in the folder name if package.json is
        unreadable.
        """
        extensions_dir = user_home / ".vscode" / "extensions"
        if ide_name == "Cursor":
            extensions_dir = user_home / ".cursor" / "extensions"

        try:
            if not extensions_dir.exists():
                return None
            for ext_dir in extensions_dir.glob(f"{self.KILOCODE_EXTENSION_ID}-*"):
                package_json = ext_dir / "package.json"
                if package_json.exists():
                    try:
                        with open(package_json, "r", encoding="utf-8") as f:
                            version = json.load(f).get("version")
                        if version:
                            return version
                    except (json.JSONDecodeError, OSError):
                        pass
                m = _VERSION_SUFFIX_RE.search(ext_dir.name)
                if m:
                    return m.group(1)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check extensions directory {extensions_dir}: {e}")
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Kilo Code is installed for a specific user.

        Walk the supported IDEs once and accept the first one that has BOTH a
        globalStorage settings dir for the kilocode extension AND a matching
        ``.app`` under ``/Applications``. We do NOT fall back to a different
        installed IDE after finding globalStorage elsewhere — that previous
        behaviour let stale globalStorage from an uninstalled IDE shadow the
        active install, and the subsequent version lookup read from the wrong
        IDE's extensions directory (returning the wrong version or falling
        through to ``"Unknown"`` even when the active IDE had a readable
        ``package.json``).
        """
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_kilocode_extension(user_home, ide_name)
            if not extension_path:
                continue
            ide_installed, _ = self._check_ide_installation(ide_name)
            if not ide_installed:
                logger.debug(
                    f"Kilo Code globalStorage found in {ide_name}, but {ide_name}.app "
                    f"is not installed — skipping (stale config)"
                )
                continue
            logger.debug(f"Found Kilo Code in {ide_name} at: {extension_path}")
            return {
                "name": self.tool_name,
                "version": self._get_extension_version_for_user(user_home, ide_name) or "Unknown",
                "install_path": str(extension_path),
            }
        logger.debug("No IDE has both Kilo Code globalStorage and an installed .app")
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

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Kilo Code extension directory exists for a specific IDE.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension directory if found, None otherwise
        """
        code_base = user_home / "Library" / "Application Support"
        extension_dir = (
            code_base / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID
        )
        
        try:
            # Check if extension directory exists
            if extension_dir.exists() and extension_dir.is_dir():
                logger.debug(
                    f"Found Kilo Code extension directory for {ide_name} at: {extension_dir}"
                )
                return extension_dir
                
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Kilo Code extension path for {ide_name}: {e}")
        
        return None

