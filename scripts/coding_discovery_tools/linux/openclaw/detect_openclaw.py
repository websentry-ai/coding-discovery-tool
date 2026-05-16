"""OpenClaw detection for Linux."""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseOpenClawDetector
from ...linux_extraction_helpers import get_linux_user_homes, is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


class LinuxOpenClawDetector(BaseOpenClawDetector):
    """Detector for OpenClaw on Linux."""

    def detect_openclaw(self) -> Optional[Dict]:
        detection_data = {
            "name": "OpenClaw",
            "is_installed": False,
            "install_path": None,
            "detection_method": None,
            "is_running": False,
            "version": None,
        }

        binary_path = self._check_binary()
        if binary_path:
            self._update_result(detection_data, binary_path, "binary_in_path")

        if not detection_data["is_installed"]:
            fs_path = self._check_system_paths()
            if fs_path:
                self._update_result(detection_data, str(fs_path), "system_path")
            else:
                user_path = self._resolve_user_paths()
                if user_path:
                    self._update_result(detection_data, str(user_path), "user_path")

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

    def _update_result(self, data: Dict, path: str, method: str) -> None:
        data["is_installed"] = True
        data["install_path"] = path
        data["detection_method"] = method
        if os.access(path, os.X_OK) and not os.path.isdir(path):
            data["version"] = self.get_version(path)

    def get_version(self, binary_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Version check failed for {binary_path}: {e}")
        return None

    def _check_binary(self) -> Optional[str]:
        return shutil.which("openclaw")

    def _check_system_paths(self) -> Optional[Path]:
        paths = [
            "/usr/local/bin/openclaw",
            "/usr/bin/openclaw",
            "/opt/openclaw",
        ]
        for p in paths:
            path_obj = Path(p)
            if path_obj.exists():
                return path_obj
        return None

    def _resolve_user_paths(self) -> Optional[Path]:
        if is_running_as_root():
            return scan_user_directories(self._check_single_user_dir)
        return self._check_single_user_dir(Path.home())

    def _check_single_user_dir(self, user_home: Path) -> Optional[Path]:
        user_paths = [
            user_home / ".openclaw" / "bin" / "openclaw",
            user_home / ".openclaw",
        ]
        for p in user_paths:
            if p.exists():
                return p
        return None

    def _check_running_process(self) -> bool:
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
            return "openclaw" in result.stdout.lower()
        except Exception:
            return False
