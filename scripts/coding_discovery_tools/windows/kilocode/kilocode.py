"""
Kilo Code detection for Windows.

Kilo Code is an AI-powered coding assistant that operates as a VS Code extension.
Detection gates on a LIVE entry in each editor's ``extensions.json`` registry, not
the ``globalStorage/<ext-id>`` dir, which survives uninstall (microsoft/vscode
#119022) and so produced phantom rows for removed extensions.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

logger = logging.getLogger(__name__)


class WindowsKiloCodeDetector(BaseToolDetector):
    """
    Detector for Kilo Code installations on Windows systems.

    Kilo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor)
    - Verifying Kilo Code extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Kilo Code extension
    SUPPORTED_IDES = ['Code', 'Cursor']

    # Kilo Code extension identifier
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"

    # Conventional Windows install dir names for each IDE under
    # ``%LOCALAPPDATA%\\Programs\\``, ``%ProgramFiles%``, and ``%ProgramFiles(x86)%``.
    # AppData survives an IDE uninstall, so we MUST verify the IDE itself
    # is still present before trusting its globalStorage — otherwise stale
    # config can dictate the wrong extensions directory for the version lookup.
    IDE_INSTALL_DIR_NAMES = {
        "Code": ("Microsoft VS Code", "Microsoft VS Code Insiders"),
        "Cursor": ("cursor", "Cursor"),
    }

    # Per-IDE .exe filenames to confirm the install dir is the real thing.
    IDE_EXE_NAMES = {
        "Code": ("Code.exe", "Code - Insiders.exe"),
        "Cursor": ("Cursor.exe", "cursor.exe"),
    }

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Kilo Code installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Per-user scan (user_home set by detect_tool_for_user): scope to THIS user only, else an
        # elevated scan enumerates every home and attributes other users' extensions to the caller.
        scoped_home = getattr(self, 'user_home', None)
        if scoped_home is not None:
            return self._check_user_for_kilocode(Path(scoped_home))

        # When running as administrator, scan all user directories first
        if self._is_running_as_admin():
            user_kilocode_info = self._scan_user_directories()
            if user_kilocode_info:
                return user_kilocode_info

        # Check current user (works for both admin and regular users)
        return self._check_user_for_kilocode(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Kilo Code version.

        Delegates to detect() so the install-gating logic stays the single
        source of truth — a leftover extension folder without a real install
        must not surface a version when detect() would report nothing.

        Returns:
            Version string if KiloCode is installed, None otherwise.
        """
        result = self.detect()
        if result:
            version = result.get("version")
            return version if version != "Unknown" else None
        return None

    def _is_running_as_admin(self) -> bool:
        """
        Check if the current process is running as administrator.
        
        Returns:
            True if running as administrator, False otherwise
        """
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                return current_user in ["administrator", "system"]
            except Exception:
                return False

    def _scan_user_directories(self) -> Optional[Dict]:
        """
        Scan all user directories for Kilo Code installations when running as admin.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        users_dir = Path("C:\\Users")
        if not users_dir.exists():
            return None
        
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    result = self._check_user_for_kilocode(user_dir)
                    if result:
                        return result
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")
                    continue
        
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

    def _check_ide_installation(self, user_home: Path, ide_name: str) -> bool:
        """
        Return True iff the given IDE is currently installed on this machine.

        Looks under the per-user ``%LOCALAPPDATA%\\Programs\\`` location plus
        the system-wide ``%ProgramFiles%`` / ``%ProgramFiles(x86)%`` roots.
        For each candidate install dir, requires the IDE's main ``.exe`` to
        still be present — directory existence alone is not enough since
        squirrel-style uninstalls can leave the parent folder behind.
        """
        for root in self._ide_search_roots(user_home):
            for dir_name in self.IDE_INSTALL_DIR_NAMES.get(ide_name, ()):
                install_dir = root / dir_name
                try:
                    if not install_dir.is_dir():
                        continue
                except (PermissionError, OSError):
                    continue
                for exe_name in self.IDE_EXE_NAMES.get(ide_name, ()):
                    try:
                        if (install_dir / exe_name).is_file():
                            return True
                    except (PermissionError, OSError):
                        continue
        return False

    def _ide_search_roots(self, user_home: Path) -> List[Path]:
        roots: List[Path] = [user_home / "AppData" / "Local" / "Programs"]
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base))
        # Standard fallbacks in case the env vars are unset (e.g. under a
        # restricted service account scanning another user's home).
        for default in (Path("C:\\Program Files"), Path("C:\\Program Files (x86)")):
            if default not in roots:
                roots.append(default)
        return roots

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

