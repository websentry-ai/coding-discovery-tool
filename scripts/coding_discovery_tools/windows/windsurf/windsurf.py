"""
Windsurf IDE detection for Windows
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT, VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsWindsurfDetector(BaseToolDetector):
    """Windsurf IDE detector for Windows systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Windsurf"

    def detect(self) -> Optional[Dict]:
        """
        Detect Windsurf installation on Windows.
        
        Returns:
            Dict with tool info or None if not found
        """
        windsurf_paths = self._get_search_paths()

        for windsurf_path in windsurf_paths:
            # Another user's profile is ACL-denied to a non-elevated scan and Path.exists()
            # re-raises EACCES; without this the machine-wide candidates are never reached.
            try:
                if not windsurf_path.exists():
                    continue

                windsurf_exe = windsurf_path / "Windsurf.exe"
                has_exe = windsurf_exe.exists()
                has_resources = (windsurf_path / "resources" / "app").exists()
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not probe Windsurf path {windsurf_path}: {e}")
                continue

            if has_exe or has_resources:
                return {
                    "name": self.tool_name,
                    "version": self._get_version_for_path(windsurf_exe) if has_exe else None,
                    "install_path": str(windsurf_path)
                }

        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Windsurf version.
        
        Returns:
            Version string or None
        """
        windsurf_paths = self._get_search_paths()
        for windsurf_path in windsurf_paths:
            windsurf_exe = windsurf_path / "Windsurf.exe"
            try:
                if windsurf_exe.exists():
                    return self._get_version_for_path(windsurf_exe)
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not probe Windsurf exe {windsurf_exe}: {e}")
                continue
        return None

    def _get_search_paths(self) -> List[Path]:
        """
        Get list of paths to search for Windsurf installation.
        
        Returns:
            List of Path objects
        """
        # Scope to this user's home when set, so a multi-user scan can't attribute another user's per-user install.
        user_home = getattr(self, 'user_home', None) or Path.home()
        return [
            user_home / "AppData" / "Local" / "Programs" / "Windsurf",
            user_home / "AppData" / "Local" / "Programs" / "windsurf",
            user_home / "AppData" / "Roaming" / "Windsurf",
            Path("C:\\Program Files") / "Windsurf",
            Path("C:\\Program Files") / "windsurf",
            Path("C:\\Program Files (x86)") / "Windsurf",
            Path("C:\\Program Files (x86)") / "windsurf",
        ]

    def _get_version_for_path(self, exe_path: Path) -> Optional[str]:
        """
        Extract Windsurf version from Windows executable.
        
        Args:
            exe_path: Path to Windsurf.exe
            
        Returns:
            Version string or None
        """
        # Try PowerShell
        version = self._get_version_via_powershell(exe_path)
        if version:
            return version

        # Try wmic
        version = self._get_version_via_wmic(exe_path)
        if version:
            return version

        # Try package.json
        version = self._get_version_from_package_json(exe_path)
        if version:
            return version

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

    def _get_version_from_package_json(self, exe_path: Path) -> Optional[str]:
        """Extract version from package.json."""
        try:
            package_json = exe_path.parent / "resources" / "app" / "package.json"
            if package_json.exists():
                with open(package_json, 'r', encoding='utf-8') as f:
                    package_data = json.load(f)
                    return package_data.get('version')
        except Exception:
            pass
        return None

