"""
Cline detection for Windows.

Cline is an AI-powered coding assistant that operates as a VS Code extension.
Detection gates on a LIVE entry in each editor's ``extensions.json`` registry, not
the ``globalStorage/<ext-id>`` dir, which survives uninstall (microsoft/vscode
#119022) and so produced phantom rows for removed extensions.

Returns detections like:
- Cline (VS Code)
- Cline (Cursor)
- Cline (Windsurf)
- Cline (Antigravity)
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...windows_extraction_helpers import is_running_as_admin, is_windows_ide_installed
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)
from ..antigravity.antigravity import WindowsAntigravityDetector

logger = logging.getLogger(__name__)


class WindowsClineDetector(BaseToolDetector):
    """
    Detector for Cline installations on Windows systems.
    
    Cline operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf, Antigravity)
    - Verifying Cline extension settings exist in IDE global storage
    - Checking Antigravity's extensions.json for installed extensions

    Returns separate detections for each IDE where Cline is installed.
    """

    # Supported IDEs that can host the Cline extension
    SUPPORTED_IDES = {
        'Code': 'VS Code',
        'Cursor': 'Cursor',
        'Windsurf': 'Windsurf',
    }

    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cline"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect Cline installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            List of dicts containing tool info for each IDE with Cline installed,
            or None if not found in any IDE
        """
        all_results = []

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        # Skip system user directories
                        if user_dir.name.lower() in ['public', 'default', 'default user', 'all users']:
                            continue
                        try:
                            user_results = self._detect_cline_for_user(user_dir)
                            all_results.extend(user_results)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            all_results = self._detect_cline_for_user(Path.home())

        return all_results if all_results else None

    def get_version(self) -> Optional[str]:
        """
        Extract Cline version.

        Returns:
            Version string or None
        """
        result = self.detect()
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get('version', 'Unknown')
        return None

    def _detect_cline_for_user(self, user_home: Path) -> List[Dict]:
        """
        Detect all Cline installations for a specific user.

        Checks each supported IDE for the Cline extension and returns
        a separate detection for each IDE where it's found.

        Args:
            user_home: User's home directory path

        Returns:
            List of dicts with tool info for each IDE with Cline installed
        """
        results = []

        # Gate on the extensions.json entry alone — no host-install AND-gate, since
        # the entry is itself proof of a live install.
        for ide_folder, ide_display_name in self.SUPPORTED_IDES.items():
            extension_info = self._check_cline_extension(user_home, ide_folder)

            if extension_info:
                _, version = extension_info
                results.append({
                    "name": f"Cline ({ide_display_name})",
                    "version": version or "Unknown",
                    "publisher": "Saoud Rizwan",
                    "ide": ide_display_name,
                    "install_path": str(extensions_dir_for_editor(user_home, ide_folder))
                })
                logger.info(f"Detected: Cline ({ide_display_name}) v{version or 'Unknown'}")

        # Antigravity keeps its own install gate (not a marketplace VS Code editor);
        # the extensions.json read still goes through the shared helper.
        if self._is_antigravity_installed(user_home):
            antigravity_info = find_extension_in_editor(
                user_home, "Antigravity", self.CLINE_EXTENSION_ID
            )
            if antigravity_info:
                _, version = antigravity_info
                results.append({
                    "name": "Cline (Antigravity)",
                    "version": version or "Unknown",
                    "publisher": "Saoud Rizwan",
                    "ide": "Antigravity",
                    "install_path": str(extensions_dir_for_editor(user_home, "Antigravity"))
                })
                logger.info(f"Detected: Cline (Antigravity) v{version or 'Unknown'}")

        return results

    def _check_ide_installation(self, ide_name: str, user_home: Path) -> Tuple[bool, Optional[str]]:
        """
        Check whether the host editor (VS Code / Cursor / Windsurf) is installed
        on Windows for the user being scanned.

        Delegates to the shared ``is_windows_ide_installed`` probe, which checks
        the user's ``%LOCALAPPDATA%\\Programs\\<IDE>`` install, machine-wide
        ``Program Files``/``Program Files (x86)``, and the editor launcher on
        PATH. ANY of those counts as installed, so a real Cline user is never
        hidden. Never raises.

        Args:
            ide_name: The ``SUPPORTED_IDES`` key (Code / Cursor / Windsurf).
            user_home: Home dir of the user being scanned.

        Returns:
            Tuple of (is_installed, install_path_or_exe_path).
        """
        try:
            return is_windows_ide_installed(ide_name, user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check {ide_name} install presence: {e}")
            return False, None

    def _is_antigravity_installed(self, user_home: Path) -> bool:
        """
        Return True iff Antigravity is installed FOR ``user_home`` — the user's
        own per-user install or a machine-wide one — so a per-user Antigravity
        owned by ANOTHER user (reachable via the all-users admin enumeration) is
        not attributed here. Wrapped so a probe error never crashes detection.
        """
        try:
            return WindowsAntigravityDetector().is_installed_for_user(user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Antigravity install presence: {e}")
            return False

    def _check_cline_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Check if Cline is a live entry in the editor's ``extensions.json`` and
        return its version.

        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE folder to check

        Returns:
            Tuple of (matched_location, version) if found, None otherwise
        """
        return find_extension_in_editor(user_home, ide_name, self.CLINE_EXTENSION_ID)
