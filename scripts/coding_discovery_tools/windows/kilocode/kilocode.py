"""
Kilo Code detection for Windows.

Kilo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Kilo Code installations by checking for:
1. IDE installations (VS Code, Cursor)
2. Kilo Code extension settings in IDE global storage directories
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Dict

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)

# Match the trailing semver portion of a VS Code extension folder name,
# including pre-release suffixes like 1.2.3-pre.5 or 1.0.0-beta.1.
_VERSION_SUFFIX_RE = re.compile(r"-(\d+\.\d+\.\d+(?:[-+][\w.+-]+)?)$")


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

        Walk the supported IDEs once and accept the first one that has BOTH a
        globalStorage settings dir for the kilocode extension AND a still-
        present IDE install on disk. The earlier shortcut (*"extension can
        only exist if IDE is installed"*) was wrong on Windows: AppData
        survives an IDE uninstall, so stale ``%AppData%\\Code\\User\\
        globalStorage\\kilocode.Kilo-Code\\`` could shadow a live Cursor
        install — and the version lookup would then read from VS Code's
        leftover ``%USERPROFILE%\\.vscode\\extensions\\`` folder instead of
        Cursor's, surfacing a stale version under a Cursor install path.
        """
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_kilocode_extension(user_home, ide_name)
            if not extension_path:
                continue
            if not self._check_ide_installation(user_home, ide_name):
                logger.debug(
                    f"Kilo Code globalStorage found in {ide_name} but {ide_name} "
                    f"is not installed — skipping (stale AppData)"
                )
                continue
            logger.debug(f"Found Kilo Code in {ide_name} at: {extension_path}")
            return {
                "name": self.tool_name,
                "version": self._get_extension_version_for_user(user_home, ide_name) or "Unknown",
                "install_path": str(extension_path),
            }
        logger.debug("No IDE has both Kilo Code globalStorage and a live install")
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

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Kilo Code extension directory exists for a specific IDE.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension directory if found, None otherwise
        """
        # Windows VS Code/Cursor global storage path
        extension_dir = (
            user_home / "AppData" / "Roaming" / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID
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

