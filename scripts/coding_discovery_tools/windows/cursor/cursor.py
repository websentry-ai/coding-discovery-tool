"""
Cursor IDE detection for Windows
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT, VERSION_TIMEOUT
from ...utils import run_command
from .cursor_rules_extractor import WindowsCursorRulesExtractor

logger = logging.getLogger(__name__)


class WindowsCursorDetector(BaseToolDetector):
    """Cursor IDE detector for Windows systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cursor"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cursor installation on Windows.
        
        Returns:
            Dict with tool info or None if not found
        """
        cursor_paths = self._get_search_paths()

        for cursor_path in cursor_paths:
            if not cursor_path.exists():
                continue

            cursor_exe = cursor_path / "Cursor.exe"
            has_resources = (cursor_path / "resources" / "app").exists()

            if cursor_exe.exists() or has_resources:
                return {
                    "name": self.tool_name,
                    "version": self._get_version_for_path(cursor_exe) if cursor_exe.exists() else None,
                    "install_path": str(cursor_path)
                }

        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Cursor version.
        
        Returns:
            Version string or None
        """
        cursor_paths = self._get_search_paths()
        for cursor_path in cursor_paths:
            cursor_exe = cursor_path / "Cursor.exe"
            if cursor_exe.exists():
                return self._get_version_for_path(cursor_exe)
        return None

    def _get_search_paths(self) -> List[Path]:
        """
        Get list of paths to search for Cursor installation.
        
        Returns:
            List of Path objects
        """
        user_home = Path.home()
        return [
            user_home / "AppData" / "Local" / "Programs" / "cursor",
            user_home / "AppData" / "Local" / "Programs" / "Cursor",
            user_home / "AppData" / "Roaming" / "Cursor",
            Path("C:\\Program Files") / "Cursor",
            Path("C:\\Program Files") / "cursor",
            Path("C:\\Program Files (x86)") / "Cursor",
            Path("C:\\Program Files (x86)") / "cursor",
        ]

    def _get_version_for_path(self, exe_path: Path) -> Optional[str]:
        """
        Extract Cursor version from Windows executable.
        
        Args:
            exe_path: Path to Cursor.exe
            
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

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.
        
        Returns:
            List of rule file dicts with metadata
        """
        extractor = WindowsCursorRulesExtractor()
        return extractor.extract_all_cursor_rules()

