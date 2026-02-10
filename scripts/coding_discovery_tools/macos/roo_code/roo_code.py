"""
Roo Code detection for macOS.

Roo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Roo Code installations by checking for:
1. IDE installations (VS Code, Cursor, Windsurf, Antigravity)
2. Roo extension settings in IDE global storage directories
3. Antigravity extensions via ~/.antigravity/extensions/extensions.json

Returns detections like:
- Roo Code (VS Code)
- Roo Code (Cursor)
- Roo Code (Windsurf)
- Roo Code (Antigravity)
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)


class MacOSRooDetector(BaseToolDetector):
    """
    Detector for Roo Code installations on macOS systems.

    Roo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf, Antigravity)
    - Verifying Roo extension settings exist in IDE global storage
    - Checking Antigravity's extensions.json for installed extensions

    Returns separate detections for each IDE where Roo Code is installed.
    """

    # Supported IDEs that can host the Roo Code extension (globalStorage-based)
    SUPPORTED_IDES = {
        'Code': 'VS Code',
        'Cursor': 'Cursor',
        'Windsurf': 'Windsurf',
    }

    # Roo Code extension identifier
    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"

    # Application names for each IDE
    IDE_APP_NAMES = {
        "Code": ["Code.app", "Visual Studio Code.app"],
        "Cursor": ["Cursor.app"],
        "Windsurf": ["Windsurf.app"],
        "Antigravity": ["Antigravity.app"],
    }

    # Standard macOS applications directory
    APPLICATIONS_DIR = Path("/Applications")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Roo Code"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect all Roo Code installations on macOS.

        When running as root, scans all user directories to find installations
        across multiple user accounts.

        Returns:
            List of dicts containing tool info for each IDE with Roo Code installed,
            or None if not found in any IDE
        """
        all_results = []

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            user_results = self._detect_roo_for_user(user_dir)
                            all_results.extend(user_results)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            all_results = self._detect_roo_for_user(Path.home())

        return all_results if all_results else None

    def get_version(self) -> Optional[str]:
        """
        Extract Roo Code version.

        Returns:
            Version string or None
        """
        result = self.detect()
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get('version', 'Unknown')
        return None

    def _detect_roo_for_user(self, user_home: Path) -> List[Dict]:
        """
        Detect all Roo Code installations for a specific user.

        Checks each supported IDE for the Roo extension and returns
        a separate detection for each IDE where it's found.

        Args:
            user_home: User's home directory path

        Returns:
            List of dicts with tool info for each IDE with Roo Code installed
        """
        results = []

        # Check globalStorage-based IDEs (VS Code, Cursor, Windsurf)
        for ide_folder, ide_display_name in self.SUPPORTED_IDES.items():
            extension_info = self._check_roo_extension(user_home, ide_folder)

            if extension_info:
                extension_path, version = extension_info

                # Verify the IDE is installed
                ide_installed, _ = self._check_ide_installation(ide_folder)

                if ide_installed or extension_path:
                    # Even if IDE app not found, extension exists so include it
                    results.append({
                        "name": f"Roo Code ({ide_display_name})",
                        "version": version or "Unknown",
                        "publisher": "Roo Veterinary Inc",
                        "ide": ide_display_name,
                        "install_path": str(extension_path)
                    })
                    logger.info(f"Detected: Roo Code ({ide_display_name}) v{version or 'Unknown'}")

        # Check Antigravity (uses different extension storage)
        antigravity_info = self._check_antigravity_extension(user_home)
        if antigravity_info:
            extension_path, version = antigravity_info
            results.append({
                "name": "Roo Code (Antigravity)",
                "version": version or "Unknown",
                "publisher": "Roo Veterinary Inc",
                "ide": "Antigravity",
                "install_path": str(extension_path)
            })
            logger.info(f"Detected: Roo Code (Antigravity) v{version or 'Unknown'}")

        return results

    def _check_ide_installation(self, ide_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a specific IDE is installed in /Applications.

        Args:
            ide_name: Name of the IDE folder (Code, Cursor, Windsurf, or Antigravity)

        Returns:
            Tuple of (is_installed: bool, install_path: Optional[str])
        """
        app_names = self.IDE_APP_NAMES.get(ide_name, [])

        for app_name in app_names:
            ide_path = self.APPLICATIONS_DIR / app_name

            try:
                if ide_path.exists() and ide_path.is_dir():
                    logger.debug(f"Found {ide_name} installation at: {ide_path}")
                    return True, str(ide_path)
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check IDE path {ide_path}: {e}")
                continue

        return False, None

    def _check_roo_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[Path, Optional[str]]]:
        """
        Check if Roo extension exists for a specific IDE and extract version.

        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE folder to check

        Returns:
            Tuple of (extension_path, version) if found, None otherwise
        """
        code_base = user_home / "Library" / "Application Support"
        extension_dir = code_base / ide_name / "User" / "globalStorage" / self.ROO_EXTENSION_ID

        try:
            if not extension_dir.exists():
                return None

            logger.debug(f"Found Roo extension directory for {ide_name} at: {extension_dir}")

            # Try to get version from package.json in the extension
            version = self._get_extension_version(user_home, ide_name)

            return extension_dir, version

        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Roo extension path for {ide_name}: {e}")

        return None

    def _check_antigravity_extension(self, user_home: Path) -> Optional[Tuple[Path, Optional[str]]]:
        """
        Check if Roo Code is installed in Antigravity.

        Antigravity stores extensions in ~/.antigravity/extensions/extensions.json

        Args:
            user_home: User's home directory path

        Returns:
            Tuple of (extension_path, version) if found, None otherwise
        """
        extensions_json = user_home / ".antigravity" / "extensions" / "extensions.json"

        try:
            if not extensions_json.exists():
                return None

            with open(extensions_json, 'r', encoding='utf-8') as f:
                extensions = json.load(f)

            # Search for Roo Code extension
            for ext in extensions:
                ext_id = ext.get('identifier', {}).get('id', '').lower()
                if ext_id == self.ROO_EXTENSION_ID.lower():
                    version = ext.get('version')
                    # Get extension path from location
                    location = ext.get('location', {})
                    ext_path = location.get('path') or location.get('fsPath')
                    if ext_path:
                        return Path(ext_path), version
                    # Fallback to relative location
                    rel_location = ext.get('relativeLocation')
                    if rel_location:
                        ext_path = user_home / ".antigravity" / "extensions" / rel_location
                        return ext_path, version

        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not check Antigravity extensions: {e}")

        return None

    def _get_extension_version(self, user_home: Path, ide_name: str) -> Optional[str]:
        """
        Try to extract Roo Code version from the extension's package.json.

        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE folder

        Returns:
            Version string if found, None otherwise
        """
        # Check extensions directory for the roo-cline extension
        extensions_dir = user_home / ".vscode" / "extensions"
        if ide_name == "Cursor":
            extensions_dir = user_home / ".cursor" / "extensions"
        elif ide_name == "Windsurf":
            extensions_dir = user_home / ".windsurf" / "extensions"

        try:
            if extensions_dir.exists():
                for ext_dir in extensions_dir.glob("rooveterinaryinc.roo-cline-*"):
                    package_json = ext_dir / "package.json"
                    if package_json.exists():
                        try:
                            with open(package_json, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                return data.get('version')
                        except (json.JSONDecodeError, OSError):
                            pass
                    # Fallback: extract version from folder name
                    if "-" in ext_dir.name:
                        try:
                            return ext_dir.name.rsplit('-', 1)[1]
                        except IndexError:
                            pass
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check extensions directory: {e}")

        return None
