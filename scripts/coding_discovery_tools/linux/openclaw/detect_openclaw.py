"""OpenClaw detection for Linux."""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseOpenClawDetector
from ...linux_extraction_helpers import get_linux_user_homes, is_running_as_root, scan_user_directories
from ...utils import resolve_npm_global_tool_bin

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
        # NOTE: the bare ``~/.openclaw`` dir is residue config/data that survives
        # uninstall — excluded. The ``~/.openclaw/bin/openclaw`` candidate is
        # also dropped: it is NOT a documented install location (npm installs to
        # the global prefix), so it never matched. The real npm binary is
        # resolved via the shared npm-prefix helper.
        npm_bin = self._resolve_npm_prefix_bin(user_home)
        if npm_bin:
            return Path(npm_bin)
        return None

    def _resolve_npm_prefix_bin(self, user_home: Path) -> Optional[str]:
        """Resolve ``openclaw`` via the npm global prefix + static fallbacks.
        Delegates to the shared ``resolve_npm_global_tool_bin`` (root-guarded
        ``npm prefix -g`` + Homebrew/pnpm/nvm fallbacks). Never raises."""
        try:
            return resolve_npm_global_tool_bin("openclaw", user_home, is_running_as_root())
        except Exception as e:  # noqa: BLE001 - resolution must never crash detection
            logger.debug(f"npm-prefix resolution for openclaw failed: {e}")
            return None

    def _check_running_process(self) -> bool:
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
            return "openclaw" in result.stdout.lower()
        except Exception:
            return False
