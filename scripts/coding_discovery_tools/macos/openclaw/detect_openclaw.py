import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseOpenClawDetector
# Importing helpers from the file you provided
from ...macos_extraction_helpers import (
    scan_user_directories,
    is_running_as_root
)

from ...mcp_extraction_helpers import (
    extract_roo_mcp_from_dir,
    walk_for_roo_mcp_configs,
    extract_ide_global_configs_with_root_support,
    read_ide_global_mcp_config,
)

logger = logging.getLogger(__name__)

class MacOSOpenClawDetector(BaseOpenClawDetector):
    """Detector for OpenClaw on macOS."""

    def detect_openclaw(self) -> Optional[Dict]:
        """
        Detect OpenClaw on macOS.
        Handles both user-level execution and root-level (MDM) execution.
        """
        detection_data = {
            "name": "OpenClaw",
            "platform": "macOS",
            "is_installed": False,
            "install_path": None,
            "detection_method": None,
            "is_running": False,
            "is_service": False,
            "version": None
        }

        # 1. Check Binary in PATH (System-wide)
        binary_path = self._check_binary()
        if binary_path:
            self._update_result(detection_data, binary_path, "binary_in_path")

        # 2. Check Static Paths (System-wide & User-specific)
        if not detection_data["is_installed"]:
            # Check system-wide paths first
            fs_path = self._check_system_paths()
            if fs_path:
                self._update_result(detection_data, str(fs_path), "system_path")
            
            # Check user-specific paths (Handles MDM/Root case)
            else:
                user_path = self._resolve_user_paths()
                if user_path:
                    self._update_result(detection_data, str(user_path), "user_path")

        # 3. Check Running Process
        if self._check_running_process():
            detection_data["is_running"] = True
            if not detection_data["is_installed"]:
                # We see it running, so it is installed somewhere
                detection_data["is_installed"] = True
                detection_data["detection_method"] = "running_process"

        # 4. Check Service
        if self._check_service():
            detection_data["is_service"] = True
            if not detection_data["is_installed"]:
                detection_data["is_installed"] = True
                detection_data["detection_method"] = "system_service"

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
        if os.access(path, os.X_OK) and not os.path.isdir(path):
             data["version"] = self.get_version(path)

    def get_version(self, binary_path: str) -> Optional[str]:
        """Get the version of the binary."""
        try:
            result = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Version check failed for {binary_path}: {e}")
        return None

    def _check_binary(self) -> Optional[str]:
        """Check if binary is in PATH."""
        return shutil.which("openclaw")

    def _check_system_paths(self) -> Optional[Path]:
        """Check known system-wide installation paths."""
        paths = [
            "/usr/local/bin/openclaw",
            "/usr/bin/openclaw",
            "/opt/openclaw",
            "/Applications/OpenClaw.app",
        ]
        for p in paths:
            path_obj = Path(p)
            if path_obj.exists():
                return path_obj
        return None

    def _resolve_user_paths(self) -> Optional[Path]:
        """
        Check user-specific paths.
        If running as root (MDM), scans /Users/* directories.
        If running as user, scans Path.home().
        """
        if is_running_as_root():
            # MDM/Root context: Use helper to scan all users
            return scan_user_directories(self._check_single_user_dir)
        else:
            # User context: Check current user's home
            return self._check_single_user_dir(Path.home())

    def _check_single_user_dir(self, user_home: Path) -> Optional[Path]:
        """
        Callback function to check for OpenClaw in a specific user's home directory.
        Used by both normal execution and scan_user_directories.
        """
        user_paths = [
            user_home / "Applications/OpenClaw.app",
            user_home / ".openclaw/bin/openclaw",
            user_home / ".openclaw"
        ]
        
        for p in user_paths:
            if p.exists():
                return p
        return None

    def _check_running_process(self) -> bool:
        """Check running processes using ps."""
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
            return "openclaw" in result.stdout.lower()
        except Exception:
            return False

    def _check_service(self) -> bool:
        """Check launchctl services."""
        try:
            result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            return "openclaw" in result.stdout.lower()
        except Exception:
            return False