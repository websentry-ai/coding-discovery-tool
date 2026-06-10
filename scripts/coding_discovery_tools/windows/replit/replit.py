"""
Replit detection for Windows.

Replit is an online IDE and coding platform. Replit Desktop ships via Electron
Forge with the Squirrel.Windows maker (``asar: true``), so it installs per-user
into ``%LocalAppData%\\<name>`` (package slug ``replit``) — NOT under
``Programs`` — with the executable in a versioned ``app-<version>\\Replit.exe``
subfolder and ``Update.exe`` in the root. ``asar: true`` packs the resource tree
into ``resources\\app.asar`` (so the legacy ``resources\\app\\package.json`` no
longer exists). This module gates on those inner install artifacts, all removed
on uninstall (unlike the ``%APPDATA%\\Roaming\\Replit`` data dir, which survives
uninstall and caused false positives).
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...windows_extraction_helpers import (
    is_running_as_admin,
    other_user_local_appdata_dirs,
    other_user_program_dirs,
)

logger = logging.getLogger(__name__)


class WindowsReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on Windows systems.

    Detection gates on a real install directory holding one of the inner
    Squirrel artifacts — ``Update.exe``, a versioned ``app-*\\Replit.exe``, a
    packed ``resources\\app.asar``, or the legacy
    ``resources\\app\\package.json`` — all removed on uninstall.
    """

    # Replit Desktop is an Electron Forge / Squirrel.Windows app. Squirrel
    # installs it per-user under %LOCALAPPDATA%\<name> directly (slug
    # ``replit``); older/system MSI-style installs may land under
    # %LOCALAPPDATA%\Programs\<name> or Program Files\<name>.
    INSTALL_DIR_NAMES = ("replit", "Replit", "replit-desktop")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit installation on Windows.

        Gate on a real install directory: one of the conventional
        ``%LOCALAPPDATA%`` (Squirrel direct install) / ``%LOCALAPPDATA%\\
        Programs`` / ``Program Files`` locations holding an inner Squirrel
        artifact — ``Update.exe``, a versioned ``app-*\\Replit.exe``, a packed
        ``resources\\app.asar``, or the legacy ``resources\\app\\package.json``.
        All are removed on uninstall. The former ``%APPDATA%\\Roaming\\Replit``
        data-dir gate was residue that survives uninstall and produced false
        positives.

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
        Return the first candidate install dir that holds a real Replit install
        artifact, all removed on uninstall:

        * ``Update.exe`` in the dir root (Squirrel writes the updater stub
          alongside the versioned ``app-*`` folders),
        * a versioned ``app-*\\Replit.exe`` (where Squirrel places the exe),
        * ``resources\\app.asar`` (Forge ``asar: true`` packs the app tree), or
        * the legacy ``resources\\app\\package.json`` (pre-asar layout).

        Never gates on the bare candidate dir merely existing — that dir is the
        one Squirrel deletes on uninstall, so requiring an inner artifact avoids
        a residue false positive. Returns None if none qualify.
        """
        for app_path in self._candidate_install_paths():
            try:
                if not app_path.is_dir():
                    continue
                if self._has_install_artifact(app_path):
                    return app_path
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check Replit install dir {app_path}: {e}")
                continue
        return None

    def _has_install_artifact(self, app_path: Path) -> bool:
        """Return True iff ``app_path`` holds an inner Squirrel install artifact
        (Update.exe / app-*\\Replit.exe / resources\\app.asar / legacy
        resources\\app\\package.json). Never raises — probes are wrapped."""
        try:
            if (app_path / "Update.exe").exists():
                return True
            if self._find_versioned_exe(app_path) is not None:
                return True
            if (app_path / "resources" / "app.asar").exists():
                return True
            if (app_path / "resources" / "app" / "package.json").exists():
                return True
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not probe Replit artifacts in {app_path}: {e}")
        return False

    @staticmethod
    def _find_versioned_exe(app_path: Path) -> Optional[Path]:
        """Return the first ``app-*\\Replit.exe`` (Squirrel versioned folder)
        under ``app_path``, or None. Never raises."""
        try:
            for exe_name in ("Replit.exe", "replit.exe"):
                for exe in app_path.glob(f"app-*/{exe_name}"):
                    if exe.exists():
                        return exe
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not glob app-* in {app_path}: {e}")
        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Replit version.

        Replit Desktop is an Electron Forge / Squirrel app with ``asar: true``,
        so the resource tree is packed into ``resources\\app.asar`` and the
        legacy ``resources\\app\\package.json`` is gone. The most reliable
        version source is therefore the Squirrel ``app-<version>`` folder name
        (e.g. ``app-1.2.3``). We parse that first, then fall back to the
        legacy ``package.json`` (older builds), then the exe's FileVersion via
        PowerShell. We deliberately do NOT parse the asar binary (zero-dep).

        Returns:
            Version string if the app is installed, None otherwise.
        """
        for app_path in self._candidate_install_paths():
            try:
                if not app_path.exists():
                    continue
            except (PermissionError, OSError):
                continue
            version = self._read_version_from_app_dir(app_path)
            if version:
                return version
            version = self._read_version_from_package_json(app_path)
            if version:
                return version
            version = self._read_version_from_exe(app_path)
            if version:
                return version
        return None

    @staticmethod
    def _read_version_from_app_dir(app_path: Path) -> Optional[str]:
        """Parse the version out of the Squirrel ``app-<version>`` folder name
        (e.g. ``app-1.2.3`` -> ``1.2.3``). Returns None if no such folder
        exists or the suffix is empty. Never raises."""
        try:
            for child in app_path.glob("app-*"):
                if not child.is_dir():
                    continue
                match = re.match(r"app-(.+)", child.name)
                if match and match.group(1):
                    return match.group(1)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not read Replit app-* version dir in {app_path}: {e}")
        return None

    def _candidate_install_paths(self) -> list:
        """
        Build the list of conventional Windows install dirs for Replit Desktop.

        Replit Desktop is an Electron Forge / Squirrel.Windows app, which
        installs per-user DIRECTLY under ``%LOCALAPPDATA%\\<name>`` (e.g.
        ``%LOCALAPPDATA%\\replit``), so that bare ``%LOCALAPPDATA%`` base is the
        primary root. ``%LOCALAPPDATA%\\Programs`` and the ``Program Files``
        env-var dirs are kept as low-priority fallbacks for older / MSI-style
        installs. We fall back to user-home-derived / ``C:\\Program Files``
        defaults when an env var is unset — matching the symmetry the Windows
        KiloCode helper already has, so a restricted service account (env
        stripped) still gets full coverage.

        Under a SYSTEM/admin scan (MDM), the scanner's own ``%LOCALAPPDATA%``
        only covers the service account. Replit installs per-user at
        ``C:\\Users\\<user>\\AppData\\Local\\replit`` (Squirrel direct) for
        OTHER users, which would otherwise be unreachable, so we also enumerate
        every real user's ``AppData\\Local`` and ``AppData\\Local\\Programs``
        dirs. The real-artifact gate in ``_find_install_dir`` (``Update.exe`` /
        ``app-*\\Replit.exe`` / ``resources\\app.asar`` / legacy
        ``resources\\app\\package.json``) still applies, so this restores
        multi-user coverage WITHOUT a residue gate.
        """
        roots = []
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            # Squirrel direct install (primary) + Programs fallback.
            roots.append(Path(local_app))
            roots.append(Path(local_app) / "Programs")
        else:
            try:
                local_default = Path.home() / "AppData" / "Local"
                roots.append(local_default)
                roots.append(local_default / "Programs")
            except (RuntimeError, OSError):
                pass
        for env, default in (
            ("ProgramFiles", Path("C:\\Program Files")),
            ("ProgramFiles(x86)", Path("C:\\Program Files (x86)")),
        ):
            base = os.environ.get(env)
            roots.append(Path(base) if base else default)

        if is_running_as_admin():
            # Other users' Squirrel direct installs (AppData\Local\<name>) AND
            # their Programs\<name> fallbacks.
            roots.extend(self._other_user_local_appdata_dirs())
            roots.extend(self._other_user_program_dirs())

        candidates = []
        for base in roots:
            for name in self.INSTALL_DIR_NAMES:
                candidates.append(base / name)
        return candidates

    @staticmethod
    def _other_user_program_dirs() -> list:
        """Per-user ``…\\AppData\\Local\\Programs`` dirs for OTHER users under an
        admin scan. Delegates to the shared helper (the enumeration logic was
        duplicated here and in the Antigravity detector)."""
        return other_user_program_dirs()

    @staticmethod
    def _other_user_local_appdata_dirs() -> list:
        """Per-user ``…\\AppData\\Local`` dirs for OTHER users under an admin
        scan — the base for Replit's Squirrel direct install
        (``AppData\\Local\\<name>``). Delegates to the shared helper."""
        return other_user_local_appdata_dirs()

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
        # The Squirrel exe lives in a versioned ``app-*\Replit.exe`` subfolder;
        # older layouts may put it directly under ``app_path``. Resolve both.
        exe = self._find_versioned_exe(app_path)
        if exe is None:
            for exe_name in ("Replit.exe", "replit.exe"):
                candidate = app_path / exe_name
                try:
                    if candidate.exists():
                        exe = candidate
                        break
                except (PermissionError, OSError):
                    continue
        if exe is None:
            return None
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
