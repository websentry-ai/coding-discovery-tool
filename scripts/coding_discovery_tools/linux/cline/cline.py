"""Cline detection for Linux."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_linux_ide_installed,
    is_running_as_root,
)

logger = logging.getLogger(__name__)


class LinuxClineDetector(BaseToolDetector):
    """Detector for Cline installations on Linux systems."""

    SUPPORTED_IDES = {
        "Code": "VS Code",
        "Cursor": "Cursor",
        "Windsurf": "Windsurf",
    }

    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"

    @property
    def tool_name(self) -> str:
        return "Cline"

    def detect(self) -> Optional[List[Dict]]:
        all_results = []
        for user_home in get_linux_user_homes():
            try:
                all_results.extend(self._detect_cline_for_user(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_home}: {e}")
        return all_results if all_results else None

    def get_version(self) -> Optional[str]:
        result = self.detect()
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get("version", "Unknown")
        return None

    def _detect_cline_for_user(self, user_home: Path) -> List[Dict]:
        results = []
        # Require BOTH the globalStorage extension dir AND the host editor to be
        # installed (mirrors macOS): the ``globalStorage/<ext-id>`` dir survives
        # an editor uninstall, so it alone is not proof of install.
        for ide_folder, ide_display_name in self.SUPPORTED_IDES.items():
            extension_info = self._check_cline_extension(user_home, ide_folder)
            if extension_info:
                extension_path, version = extension_info
                host_installed, _ = self._check_ide_installation(ide_folder, user_home)
                if host_installed and extension_path:
                    results.append({
                        "name": f"Cline ({ide_display_name})",
                        "version": version or "Unknown",
                        "publisher": "Saoud Rizwan",
                        "ide": ide_display_name,
                        "install_path": str(extension_path),
                    })
                    logger.info(f"Detected: Cline ({ide_display_name}) v{version or 'Unknown'}")
        return results

    def _check_ide_installation(self, ide_name: str, user_home: Path) -> Tuple[bool, Optional[str]]:
        """
        Check whether the host editor (VS Code / Cursor / Windsurf) is installed
        on Linux for the user being scanned.

        Delegates to the shared ``is_linux_ide_installed`` probe, which checks
        system dirs, ``/opt`` and ``~/.local/share`` sideloads, Snap, Flatpak,
        ``.desktop`` launchers, and the editor binary on PATH. ANY of those
        counts as installed, so a real Cline user is never hidden. Never raises.

        Args:
            ide_name: The ``SUPPORTED_IDES`` key (Code / Cursor / Windsurf).
            user_home: Home dir of the user being scanned.

        Returns:
            Tuple of (is_installed, path).
        """
        try:
            return is_linux_ide_installed(ide_name, user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check {ide_name} install presence: {e}")
            return False, None

    def _check_cline_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[Path, Optional[str]]]:
        extension_dir = (
            user_home / ".config" / ide_name / "User" / "globalStorage" / self.CLINE_EXTENSION_ID
        )
        try:
            if not extension_dir.exists():
                return None
            logger.debug(f"Found Cline extension directory for {ide_name} at: {extension_dir}")
            version = self._get_extension_version(user_home, ide_name)
            return extension_dir, version
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Cline extension path for {ide_name}: {e}")
        return None

    def _get_extension_version(self, user_home: Path, ide_name: str) -> Optional[str]:
        extensions_dir = user_home / ".vscode" / "extensions"
        if ide_name == "Cursor":
            extensions_dir = user_home / ".cursor" / "extensions"
        elif ide_name == "Windsurf":
            extensions_dir = user_home / ".windsurf" / "extensions"

        try:
            if extensions_dir.exists():
                for ext_dir in extensions_dir.glob("saoudrizwan.claude-dev-*"):
                    package_json = ext_dir / "package.json"
                    if package_json.exists():
                        try:
                            with open(package_json, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                return data.get("version")
                        except (json.JSONDecodeError, OSError):
                            pass
                    if "-" in ext_dir.name:
                        try:
                            return ext_dir.name.rsplit("-", 1)[1]
                        except IndexError:
                            pass
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check extensions directory: {e}")
        return None
