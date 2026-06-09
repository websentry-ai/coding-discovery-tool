"""
Antigravity (Google Gemini) detection for Windows
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT, VERSION_TIMEOUT
from ...utils import run_command
from ...windows_extraction_helpers import is_running_as_admin, other_user_program_dirs

logger = logging.getLogger(__name__)


class WindowsAntigravityDetector(BaseToolDetector):
    """Antigravity (Google Gemini) detector for Windows systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Antigravity"

    def detect(self) -> Optional[Dict]:
        """
        Detect Antigravity installation on Windows.

        Gate purely on the installed application — ``_find_app_path()`` checks
        ``Programs/`` and ``Program Files/`` for the ``Antigravity.exe`` or the
        ``resources`` tree, both of which are removed on uninstall. The former
        ``~/.antigravity`` fallback was a residue config/data dir that survives
        uninstall and produced false positives.

        Returns:
            Dict with tool info or None if not found
        """
        # Check for installed application
        app_path = self._find_app_path()
        if app_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(app_path),
                "install_path": str(app_path)
            }

        return None

    def _find_app_path(self) -> Optional[Path]:
        """
        Find the Antigravity application path.
        
        Returns:
            Path to the app if found, None otherwise
        """
        for app_path in self._get_search_paths():
            if app_path.exists():
                # Check for executable or app directory
                exe_path = app_path / "Antigravity.exe"
                if exe_path.exists():
                    return app_path
                # Check if it's a directory with resources
                if app_path.is_dir() and (app_path / "resources").exists():
                    return app_path
        return None

    # Per-user squirrel install dir names under ...\AppData\Local\Programs.
    _PROGRAM_DIR_NAMES = ("antigravity", "Antigravity", "Gemini", "Google Gemini")

    def _get_search_paths(self) -> List[Path]:
        """
        Get list of paths to search for Antigravity installation.

        Under a SYSTEM/admin scan (MDM), the scanner's own ``Path.home()``
        only covers the service account. Antigravity (a VS Code fork) installs
        per-user under ``C:\\Users\\<user>\\AppData\\Local\\Programs`` for OTHER
        users, which would otherwise be unreachable, so we also enumerate every
        real user's ``Programs`` dir. The real-artifact gate in
        ``_find_app_path`` (``Antigravity.exe`` / ``resources`` tree) still
        applies, restoring multi-user coverage WITHOUT a residue gate.

        Returns:
            List of Path objects
        """
        try:
            user_home = Path.home()
        except (RuntimeError, OSError):
            user_home = None
        paths = []
        if user_home is not None:
            paths.extend([
                user_home / "AppData" / "Local" / "Programs" / "antigravity",
                user_home / "AppData" / "Local" / "Programs" / "Antigravity",
                user_home / "AppData" / "Local" / "Programs" / "Gemini",
                user_home / "AppData" / "Local" / "Programs" / "Google Gemini",
                user_home / "AppData" / "Roaming" / "antigravity",
                user_home / "AppData" / "Roaming" / "Antigravity",
            ])
        paths.extend([
            Path("C:\\Program Files") / "Antigravity",
            Path("C:\\Program Files") / "antigravity",
            Path("C:\\Program Files") / "Gemini",
            Path("C:\\Program Files") / "Google Gemini",
            Path("C:\\Program Files (x86)") / "Antigravity",
            Path("C:\\Program Files (x86)") / "antigravity",
            Path("C:\\Program Files (x86)") / "Gemini",
            Path("C:\\Program Files (x86)") / "Google Gemini",
        ])

        if is_running_as_admin():
            for program_root in self._other_user_program_dirs():
                for name in self._PROGRAM_DIR_NAMES:
                    paths.append(program_root / name)

        return paths

    @staticmethod
    def _other_user_program_dirs() -> List[Path]:
        """Per-user ``…\\AppData\\Local\\Programs`` dirs for OTHER users under an
        admin scan. Delegates to the shared helper (the enumeration logic was
        duplicated here and in the Replit detector)."""
        return other_user_program_dirs()

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """
        Extract Antigravity version from Windows executable or package.json.
        
        Args:
            app_path: Optional path to the app (if None, will try to find it)
        
        Returns:
            Version string or None
        """
        if app_path is None:
            app_path = self._find_app_path()
        
        if app_path is None:
            return None
        
        try:
            # Try to get version from executable
            exe_path = app_path / "Antigravity.exe"
            if exe_path.exists():
                # Try PowerShell
                version = self._get_version_via_powershell(exe_path)
                if version:
                    return version
                
                # Try wmic
                version = self._get_version_via_wmic(exe_path)
                if version:
                    return version
            
            # Try package.json
            version = self._get_version_from_package_json(app_path)
            if version:
                return version
        except Exception as e:
            logger.warning(f"Could not extract Antigravity version: {e}")
        
        return None

    def _get_version_via_powershell(self, exe_path: Path) -> Optional[str]:
        """Get file version using PowerShell."""
        ps_command = f'(Get-Item {repr(str(exe_path))}).VersionInfo.FileVersion'
        return run_command(["powershell", "-Command", ps_command], COMMAND_TIMEOUT)

    def _get_version_via_wmic(self, exe_path: Path) -> Optional[str]:
        """Get file version using wmic."""
        try:
            escaped_path = str(exe_path).replace('\\', '\\\\')
            output = run_command(
                ["wmic", "datafile", "where", f"name='{escaped_path}'", "get", "Version"],
                COMMAND_TIMEOUT
            )
            if output:
                lines = output.split('\n')
                if len(lines) > 1:
                    version = lines[1].strip()
                    if version:
                        return version
        except Exception as e:
            logger.warning(f"Could not extract version via wmic: {e}")
        return None

    def _get_version_from_package_json(self, app_path: Path) -> Optional[str]:
        """Extract version from package.json."""
        try:
            import json
            package_json = app_path / "resources" / "app" / "package.json"
            if package_json.exists():
                with open(package_json, 'r', encoding='utf-8') as f:
                    package_data = json.load(f)
                    return package_data.get('version')
        except Exception:
            pass
        return None

