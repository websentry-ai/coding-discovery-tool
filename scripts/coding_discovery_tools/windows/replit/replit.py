"""
Replit detection for Windows.

Replit is an online IDE and coding platform.
This module detects Replit installations by checking for:
User data directory in %APPDATA%\\Replit\\ (AppData\\Roaming\\Replit)
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on Windows systems.

    Detection involves:
    - Checking if user data directory exists in AppData\\Roaming\\Replit
    """

    # User data directory name
    USER_DATA_DIR_NAME = "Replit"

    # Replit Desktop is an Electron app; squirrel.windows installs it
    # per-user under %LOCALAPPDATA%\Programs\<name> (newer builds drop the
    # -desktop suffix). System-wide MSI installs land under Program Files.
    INSTALL_DIR_NAMES = ("Replit", "replit-desktop", "replit")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        user_data_path = self._check_user_data_directory()
        if user_data_path:
            return {
                "name": self.tool_name,
                # ``or "Unknown"`` keeps the version field consistent with
                # KiloCode — without it, a data-dir-only install emits
                # ``"version": null`` while KiloCode emits ``"Unknown"``.
                "version": self.get_version() or "Unknown",
                "install_path": str(user_data_path)
            }
        
        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Replit version.

        Replit Desktop is a standard Electron app, so we walk the same
        install locations Antigravity/Cursor do — ``%LOCALAPPDATA%\\Programs``
        (per-user squirrel install) and ``Program Files`` (system install) —
        and read ``resources\\app\\package.json``. If that fails, fall back
        to the .exe's FileVersion via PowerShell.

        Returns:
            Version string if the app is installed, None otherwise.
        """
        for app_path in self._candidate_install_paths():
            try:
                if not app_path.exists():
                    continue
            except (PermissionError, OSError):
                continue
            version = self._read_version_from_package_json(app_path)
            if version:
                return version
            version = self._read_version_from_exe(app_path)
            if version:
                return version
        return None

    def _candidate_install_paths(self) -> list:
        candidates = []
        local_app = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        for base in (local_app and Path(local_app) / "Programs", program_files, program_files_x86):
            if not base:
                continue
            base = Path(base)
            for name in self.INSTALL_DIR_NAMES:
                candidates.append(base / name)
        return candidates

    def _read_version_from_package_json(self, app_path: Path) -> Optional[str]:
        pkg = app_path / "resources" / "app" / "package.json"
        try:
            if pkg.exists():
                with open(pkg, "r", encoding="utf-8") as f:
                    return json.load(f).get("version")
        except (json.JSONDecodeError, OSError, PermissionError) as e:
            logger.debug(f"Could not read Replit package.json at {pkg}: {e}")
        return None

    def _read_version_from_exe(self, app_path: Path) -> Optional[str]:
        for exe_name in ("Replit.exe", "replit.exe"):
            exe = app_path / exe_name
            try:
                if not exe.exists():
                    continue
            except (PermissionError, OSError):
                continue
            try:
                # Quote the path as a PowerShell single-quoted string. Inside
                # single quotes, backslashes are literal — so we just need to
                # escape any embedded single quotes by doubling them. Don't use
                # Python's repr(): it produces ``'C:\\Users\\…'`` (with double
                # backslashes) which PowerShell would treat as literal ``\\``.
                escaped = str(exe).replace("'", "''")
                ps_command = (
                    f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion"
                )
                output = run_command(["powershell", "-Command", ps_command], VERSION_TIMEOUT)
                if output:
                    output = output.strip()
                    if output:
                        return output
            except Exception as e:
                logger.debug(f"PowerShell version lookup failed for {exe}: {e}")
        return None

    def _check_user_data_directory(self) -> Optional[Path]:
        """
        Check if Replit user data directory exists.
        
        When running as administrator, scans all user directories.
        Otherwise, checks current user's directory.
        
        Returns:
            Path to user data directory if found, None otherwise
        """
        # When running as administrator, scan all user directories
        if self._is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            user_data_path = self._get_user_data_path(user_dir)
                            if user_data_path:
                                return user_data_path
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        
        # Check current user's directory
        user_data_path = self._get_user_data_path(Path.home())
        if user_data_path:
            return user_data_path
        
        return None

    def _get_user_data_path(self, user_home: Path) -> Optional[Path]:
        """
        Get Replit user data directory path for a specific user.
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Path to user data directory if it exists, None otherwise
        """
        user_data_path = user_home / "AppData" / "Roaming" / self.USER_DATA_DIR_NAME
        
        try:
            if user_data_path.exists() and user_data_path.is_dir():
                logger.debug(f"Found Replit user data at: {user_data_path}")
                return user_data_path
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Replit user data path {user_data_path}: {e}")
        
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
