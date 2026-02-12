"""
OpenClaw detection for Windows.

OpenClaw is an AI-powered coding assistant.
This module detects OpenClaw installations on Windows systems.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseOpenClawDetector
from ...windows_extraction_helpers import (
    is_running_as_admin,
)

logger = logging.getLogger(__name__)


class WindowsOpenClawDetector(BaseOpenClawDetector):
    """Detector for OpenClaw on Windows."""

    def detect_openclaw(self) -> Optional[Dict]:
        """
        Detect OpenClaw on Windows.
        Handles both user-level execution and admin-level (MDM) execution.
        """
        detection_data = {
            "name": "OpenClaw",
            "platform": "Windows",
            "is_installed": False,
            "install_path": None,
            "detection_method": None,
            "is_running": False,
            "version": None
        }

        # 1. Check Binary in PATH
        binary_path = self._check_binary()
        if binary_path:
            self._update_result(detection_data, binary_path, "binary_in_path")

        # 2. Check Static Paths (System-wide & User-specific)
        if not detection_data["is_installed"]:
            fs_path = self._check_installation_paths()
            if fs_path:
                self._update_result(detection_data, str(fs_path), "static_path")

        # 3. Deep search for openclaw.exe in major directories
        if not detection_data["is_installed"]:
            exe_path = self._search_for_executable()
            if exe_path:
                self._update_result(detection_data, str(exe_path), "executable_search")

        # 4. Check Running Process
        if self._check_running_process():
            detection_data["is_running"] = True
            if not detection_data["is_installed"]:
                detection_data["is_installed"] = True
                detection_data["detection_method"] = "running_process"

        if detection_data["is_installed"]:
            return {
                "name": detection_data["name"],
                "version": detection_data["version"],
                "install_path": detection_data["install_path"],
                "projects": [],
            }

        return None

    def _update_result(self, data: Dict, path: str, method: str):
        """Helper to update the detection result dict."""
        data["is_installed"] = True
        data["install_path"] = path
        data["detection_method"] = method

        # Try to get version if it's an executable file
        if path and path.endswith(".exe") and os.path.isfile(path):
            data["version"] = self.get_version(path)

    def get_version(self, binary_path: str = None) -> Optional[str]:
        """Get the version of the binary."""
        try:
            cmd = [binary_path, "--version"] if binary_path else ["openclaw", "--version"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                shell=True  # Required for Windows
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                if output:
                    return output
        except Exception as e:
            logger.debug(f"Version check failed: {e}")
        return None

    def _check_binary(self) -> Optional[str]:
        """Check if binary is in PATH."""
        return shutil.which("openclaw") or shutil.which("openclaw.exe")

    def _get_installation_paths(self) -> List[Path]:
        """Get list of paths to check for OpenClaw installation."""
        paths = []

        # User-specific paths
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        userprofile = os.environ.get("USERPROFILE", "")

        if local_appdata:
            paths.append(Path(local_appdata) / "Programs" / "OpenClaw")
            paths.append(Path(local_appdata) / "OpenClaw")

        if appdata:
            paths.append(Path(appdata) / "OpenClaw")

        if userprofile:
            paths.append(Path(userprofile) / ".openclaw")

        # System-wide paths
        paths.append(Path(r"C:\Program Files\OpenClaw"))
        paths.append(Path(r"C:\Program Files (x86)\OpenClaw"))

        # If running as admin, also check other users' directories
        if is_running_as_admin():
            users_dir = Path(r"C:\Users")
            if users_dir.exists():
                try:
                    for user_dir in users_dir.iterdir():
                        if user_dir.is_dir() and not user_dir.name.startswith('.'):
                            # Skip system directories
                            if user_dir.name.lower() in ['public', 'default', 'default user', 'all users']:
                                continue
                            paths.append(user_dir / "AppData" / "Local" / "Programs" / "OpenClaw")
                            paths.append(user_dir / "AppData" / "Local" / "OpenClaw")
                            paths.append(user_dir / "AppData" / "Roaming" / "OpenClaw")
                            paths.append(user_dir / ".openclaw")
                except (PermissionError, OSError) as e:
                    logger.debug(f"Error scanning user directories: {e}")

        return paths

    def _check_installation_paths(self) -> Optional[Path]:
        """Check known installation paths."""
        for path in self._get_installation_paths():
            if path.exists():
                logger.debug(f"Found OpenClaw at: {path}")
                return path
        return None

    def _search_for_executable(self) -> Optional[Path]:
        """
        Search for openclaw.exe in major directories.
        This is a deeper search when static paths don't find it.
        """
        search_roots = []

        local_appdata = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")

        if local_appdata and os.path.exists(local_appdata):
            search_roots.append(Path(local_appdata))

        if appdata and os.path.exists(appdata):
            search_roots.append(Path(appdata))

        # Also check Program Files
        program_files = Path(r"C:\Program Files")
        program_files_x86 = Path(r"C:\Program Files (x86)")

        if program_files.exists():
            search_roots.append(program_files)
        if program_files_x86.exists():
            search_roots.append(program_files_x86)

        for root in search_roots:
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    # Skip common directories that slow down search
                    dirnames[:] = [d for d in dirnames if d.lower() not in [
                        'node_modules', '.git', '__pycache__', 'cache',
                        'temp', 'tmp', 'logs', 'log'
                    ]]

                    if "openclaw.exe" in [f.lower() for f in filenames]:
                        # Find the actual filename with correct case
                        for f in filenames:
                            if f.lower() == "openclaw.exe":
                                full_path = Path(dirpath) / f
                                logger.debug(f"Found openclaw.exe at: {full_path}")
                                return full_path
            except (PermissionError, OSError) as e:
                logger.debug(f"Error searching {root}: {e}")
                continue

        return None

    def _check_running_process(self) -> bool:
        """Check if OpenClaw process is running using tasklist."""
        try:
            result = subprocess.run(
                ["tasklist"],
                capture_output=True,
                text=True,
                check=False
            )
            return "openclaw.exe" in result.stdout.lower()
        except Exception as e:
            logger.debug(f"Could not check running processes: {e}")
            return False
