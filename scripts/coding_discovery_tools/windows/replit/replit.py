"""
Replit detection for Windows.

Replit is an online IDE and coding platform.
This module detects Replit installations by checking for a real install
directory under %LOCALAPPDATA%\\Programs or Program Files that contains
Replit.exe or a resources\\app\\package.json resource tree. These artifacts are
removed on uninstall (unlike the %APPDATA%\\Roaming\\Replit data dir, which
survives uninstall and caused false positives).
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...windows_extraction_helpers import is_running_as_admin

logger = logging.getLogger(__name__)


class WindowsReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on Windows systems.

    Detection gates on a real install directory containing Replit.exe or a
    resources\\app\\package.json resource tree, both removed on uninstall.
    """

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

        Gate on a real install directory: one of the conventional
        ``%LOCALAPPDATA%\\Programs`` / ``Program Files`` locations that contains
        ``Replit.exe`` or a ``resources\\app\\package.json`` resource tree. Both
        artifacts are removed on uninstall. The former
        ``%APPDATA%\\Roaming\\Replit`` data-dir gate was residue that survives
        uninstall and produced false positives.

        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        install_path = self._find_install_dir()
        if install_path:
            return {
                "name": self.tool_name,
                # ``or "Unknown"`` keeps the version field consistent with
                # KiloCode — without it the version field could be ``null``
                # while KiloCode emits ``"Unknown"``.
                "version": self.get_version() or "Unknown",
                "install_path": str(install_path)
            }

        return None

    def _find_install_dir(self) -> Optional[Path]:
        """
        Return the first candidate install dir that holds a real Replit
        install artifact (``Replit.exe`` or ``resources\\app\\package.json``),
        which is removed on uninstall. Returns None if none qualify.
        """
        for app_path in self._candidate_install_paths():
            try:
                if not app_path.is_dir():
                    continue
                for exe_name in ("Replit.exe", "replit.exe"):
                    if (app_path / exe_name).exists():
                        return app_path
                if (app_path / "resources" / "app" / "package.json").exists():
                    return app_path
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check Replit install dir {app_path}: {e}")
                continue
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
        """
        Build the list of conventional Windows install dirs for Replit Desktop.

        Reads ``%LOCALAPPDATA%`` and the Program Files env vars, falling back
        to user-home-derived / ``C:\\Program Files`` defaults when any of
        them are unset — matching the symmetry the Windows KiloCode helper
        already has, so a restricted service account (env stripped) still
        gets full coverage.

        Under a SYSTEM/admin scan (MDM), the scanner's own
        ``%LOCALAPPDATA%`` only covers the service account. squirrel.windows
        installs Replit per-user at ``C:\\Users\\<user>\\AppData\\Local\\
        Programs\\Replit`` for OTHER users, which would otherwise be
        unreachable, so we also enumerate every real user's ``Programs`` dir.
        The real-artifact gate in ``_find_install_dir`` (``Replit.exe`` /
        ``resources\\app\\package.json``) still applies, so this restores
        multi-user coverage WITHOUT a residue gate.
        """
        roots = []
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            roots.append(Path(local_app) / "Programs")
        else:
            try:
                roots.append(Path.home() / "AppData" / "Local" / "Programs")
            except (RuntimeError, OSError):
                pass
        for env, default in (
            ("ProgramFiles", Path("C:\\Program Files")),
            ("ProgramFiles(x86)", Path("C:\\Program Files (x86)")),
        ):
            base = os.environ.get(env)
            roots.append(Path(base) if base else default)

        if is_running_as_admin():
            roots.extend(self._other_user_program_dirs())

        candidates = []
        for base in roots:
            for name in self.INSTALL_DIR_NAMES:
                candidates.append(base / name)
        return candidates

    @staticmethod
    def _other_user_program_dirs() -> list:
        """
        Enumerate ``C:\\Users\\<user>\\AppData\\Local\\Programs`` for every
        real user, so a SYSTEM/admin (MDM) scan reaches per-user squirrel
        installs that belong to other users. Skips the well-known service /
        template accounts. Never raises — directory enumeration is wrapped.
        """
        program_roots = []
        users_dir = Path("C:\\Users")
        try:
            if not users_dir.exists():
                return program_roots
            for user_dir in users_dir.iterdir():
                try:
                    if not user_dir.is_dir() or user_dir.name.startswith("."):
                        continue
                    if user_dir.name.lower() in (
                        "public", "default", "default user", "all users",
                    ):
                        continue
                    program_roots.append(
                        user_dir / "AppData" / "Local" / "Programs"
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Could not inspect user dir {user_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not enumerate C:\\Users for Replit: {e}")
        return program_roots

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
