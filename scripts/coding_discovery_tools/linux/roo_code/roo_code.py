"""Roo Code detection for Linux."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


class LinuxRooDetector(BaseToolDetector):
    """Detector for Roo Code installations on Linux systems."""

    SUPPORTED_IDES = {
        "Code": "VS Code",
        "Cursor": "Cursor",
        "Windsurf": "Windsurf",
    }

    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"

    @property
    def tool_name(self) -> str:
        return "Roo Code"

    def detect(self) -> Optional[List[Dict]]:
        all_results = []
        for user_home in get_linux_user_homes():
            try:
                all_results.extend(self._detect_roo_for_user(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_home}: {e}")
        return all_results if all_results else None

    def get_version(self) -> Optional[str]:
        result = self.detect()
        if result and isinstance(result, list) and len(result) > 0:
            return result[0].get("version", "Unknown")
        return None

    def _detect_roo_for_user(self, user_home: Path) -> List[Dict]:
        results = []
        for ide_folder, ide_display_name in self.SUPPORTED_IDES.items():
            extension_info = self._check_roo_extension(user_home, ide_folder)
            if extension_info:
                extension_path, version = extension_info
                results.append({
                    "name": f"Roo Code ({ide_display_name})",
                    "version": version or "Unknown",
                    "publisher": "Roo Veterinary Inc",
                    "ide": ide_display_name,
                    "install_path": str(extension_path),
                })
                logger.info(f"Detected: Roo Code ({ide_display_name}) v{version or 'Unknown'}")
        return results

    def _check_roo_extension(self, user_home: Path, ide_name: str) -> Optional[Tuple[Path, Optional[str]]]:
        extension_dir = (
            user_home / ".config" / ide_name / "User" / "globalStorage" / self.ROO_EXTENSION_ID
        )
        try:
            if not extension_dir.exists():
                return None
            logger.debug(f"Found Roo extension directory for {ide_name} at: {extension_dir}")
            version = self._get_extension_version(user_home, ide_name)
            return extension_dir, version
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Roo extension path for {ide_name}: {e}")
        return None

    def _get_extension_version(self, user_home: Path, ide_name: str) -> Optional[str]:
        extensions_dir = user_home / ".vscode" / "extensions"
        if ide_name == "Cursor":
            extensions_dir = user_home / ".cursor" / "extensions"
        elif ide_name == "Windsurf":
            extensions_dir = user_home / ".windsurf" / "extensions"

        try:
            if extensions_dir.exists():
                for ext_dir in extensions_dir.glob("rooveterinaryinc.roo-cline-*"):
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
