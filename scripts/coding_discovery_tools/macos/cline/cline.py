"""
Cline detection for macOS.

Cline is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Cline installations by checking, for each supported editor,
whether the Cline extension is a LIVE entry in that editor's
``extensions.json`` install registry (VS Code rewrites this file on uninstall).

The extension's ``globalStorage/<ext-id>`` directory is deliberately NOT used as
the gate: VS Code does not clean it up on uninstall (microsoft/vscode#119022),
so gating on it surfaced phantom rows for extensions the user had removed. The
host-editor ``.app`` AND-gate is likewise dropped — the ``extensions.json`` entry
is itself proof the editor manages a live install.

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
from ...macos_extraction_helpers import is_running_as_root
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

logger = logging.getLogger(__name__)


class MacOSClineDetector(BaseToolDetector):
    """
    Detector for Cline installations on macOS systems.

    Cline operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf, Antigravity)
    - Verifying Cline extension settings exist in IDE global storage
    - Checking Antigravity's extensions.json for installed extensions

    Returns separate detections for each IDE where Cline is installed.
    """

    SUPPORTED_IDES = {
        'Code': 'VS Code',
        'Cursor': 'Cursor',
        'Windsurf': 'Windsurf',
    }

    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"

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
        return "Cline"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect all Cline installations on macOS.

        When running as root, scans all user directories to find installations
        across multiple user accounts.

        Returns:
            List of dicts containing tool info for each IDE with Cline installed,
            or None if not found in any IDE
        """
        all_results = []

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
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

        # Gate purely on the editor's extensions.json registry entry (which VS
        # Code rewrites on uninstall). No host-.app AND-gate: the registry entry
        # is itself proof of a live install, and globalStorage residue — which
        # survives uninstall — no longer drives detection.
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

        # Antigravity keeps its own install gate (the .app being present) because
        # it is not a marketplace VS Code editor; its extensions.json read is
        # routed through the shared helper so the live-entry semantics match.
        antigravity_installed, _ = self._check_ide_installation("Antigravity", user_home)
        if antigravity_installed:
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

    def _check_ide_installation(
        self, ide_name: str, user_home: Optional[Path] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a specific IDE is installed in the machine-wide
        ``/Applications`` OR the user-local ``~/Applications`` — the latter
        covers per-user .app installs (a drag-install into the home folder).
        Checking both avoids a false negative that would hide a real Cline user
        whose editor lives in ``~/Applications``.

        Args:
            ide_name: Name of the IDE folder (Code, Cursor, Windsurf, or Antigravity)
            user_home: Home dir of the user being scanned (for ``~/Applications``).
                Defaults to ``Path.home()`` when not supplied.

        Returns:
            Tuple of (is_installed: bool, install_path: Optional[str])
        """
        app_names = self.IDE_APP_NAMES.get(ide_name, [])
        if user_home is None:
            user_home = Path.home()
        app_dirs = [self.APPLICATIONS_DIR, user_home / "Applications"]

        for app_dir in app_dirs:
            for app_name in app_names:
                ide_path = app_dir / app_name
                try:
                    if ide_path.exists() and ide_path.is_dir():
                        logger.debug(f"Found {ide_name} installation at: {ide_path}")
                        return True, str(ide_path)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Could not check IDE path {ide_path}: {e}")
                    continue

        return False, None

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
