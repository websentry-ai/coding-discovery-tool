"""
Cursor IDE detection for Linux
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT, COMMAND_TIMEOUT
from ...utils import run_command
from .cursor_rules_extractor import LinuxCursorRulesExtractor

logger = logging.getLogger(__name__)


class LinuxCursorDetector(BaseToolDetector):
    """Cursor IDE detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cursor"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cursor installation on Linux.

        Returns:
            Dict with tool info or None if not found
        """
        cursor_paths = self._get_search_paths()

        for cursor_path in cursor_paths:
            if not cursor_path.exists():
                continue

            # Check for Cursor executable or AppImage
            cursor_exe = cursor_path / "cursor"
            cursor_appimage = cursor_path / "Cursor.AppImage"
            has_resources = (cursor_path / "resources" / "app").exists()

            if cursor_exe.exists() or cursor_appimage.exists() or has_resources:
                executable = cursor_appimage if cursor_appimage.exists() else cursor_exe
                return {
                    "name": self.tool_name,
                    "version": self._get_version_for_path(cursor_path, executable) if executable.exists() else None,
                    "install_path": str(cursor_path)
                }

        # Check if cursor is in PATH
        cursor_in_path = self._check_in_path()
        if cursor_in_path:
            return cursor_in_path

        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Cursor version.

        Returns:
            Version string or None
        """
        cursor_paths = self._get_search_paths()
        for cursor_path in cursor_paths:
            cursor_exe = cursor_path / "cursor"
            cursor_appimage = cursor_path / "Cursor.AppImage"

            executable = None
            if cursor_appimage.exists():
                executable = cursor_appimage
            elif cursor_exe.exists():
                executable = cursor_exe

            if executable:
                version = self._get_version_for_path(cursor_path, executable)
                if version:
                    return version

        # Try from PATH
        return self._get_version_from_command()

    def _get_search_paths(self) -> List[Path]:
        """
        Get list of paths to search for Cursor installation.

        Returns:
            List of Path objects
        """
        user_home = Path.home()
        return [
            user_home / ".cursor",
            user_home / ".local" / "share" / "cursor",
            user_home / ".local" / "share" / "applications" / "cursor",
            user_home / "Applications" / "cursor",
            user_home / "Applications" / "Cursor",
            Path("/opt") / "cursor",
            Path("/opt") / "Cursor",
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            user_home / "bin",
            user_home / ".local" / "bin",
        ]

    def _check_in_path(self) -> Optional[Dict]:
        """Check if cursor is in PATH."""
        output = run_command(["which", "cursor"], COMMAND_TIMEOUT)
        if output:
            cursor_path = Path(output.strip())
            if cursor_path.exists():
                return {
                    "name": self.tool_name,
                    "version": self._get_version_from_command(),
                    "install_path": str(cursor_path)
                }
        return None

    def _get_version_for_path(self, base_path: Path, executable: Path) -> Optional[str]:
        """
        Extract Cursor version for a specific installation path.

        Args:
            base_path: Base installation path
            executable: Path to the executable

        Returns:
            Version string or None
        """
        # Try running the executable with --version
        if executable.exists():
            try:
                output = run_command([str(executable), "--version"], VERSION_TIMEOUT)
                if output:
                    # Extract version number from output
                    for line in output.split('\n'):
                        if line.strip():
                            return line.strip()
            except Exception as e:
                logger.debug(f"Could not get version from executable: {e}")

        # Try package.json
        version = self._get_version_from_package_json(base_path)
        if version:
            return version

        # Try desktop file
        version = self._get_version_from_desktop_file()
        if version:
            return version

        return None

    def _get_version_from_command(self) -> Optional[str]:
        """Get version by running cursor command."""
        try:
            output = run_command(["cursor", "--version"], VERSION_TIMEOUT)
            if output:
                for line in output.split('\n'):
                    if line.strip():
                        return line.strip()
        except Exception as e:
            logger.debug(f"Could not get version from cursor command: {e}")
        return None

    def _get_version_from_package_json(self, base_path: Path) -> Optional[str]:
        """Extract version from package.json."""
        try:
            package_json = base_path / "resources" / "app" / "package.json"
            if package_json.exists():
                with open(package_json, 'r', encoding='utf-8') as f:
                    package_data = json.load(f)
                    return package_data.get('version')
        except Exception:
            pass
        return None

    def _get_version_from_desktop_file(self) -> Optional[str]:
        """Try to extract version from desktop file if it exists."""
        desktop_paths = [
            Path.home() / ".local" / "share" / "applications" / "cursor.desktop",
            Path("/usr/share/applications") / "cursor.desktop",
        ]

        for desktop_path in desktop_paths:
            if desktop_path.exists():
                try:
                    with open(desktop_path, 'r') as f:
                        for line in f:
                            if line.startswith("Version="):
                                return line.split("=", 1)[1].strip()
                except Exception:
                    pass
        return None

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.

        Returns:
            List of rule file dicts with metadata
        """
        extractor = LinuxCursorRulesExtractor()
        return extractor.extract_all_cursor_rules()