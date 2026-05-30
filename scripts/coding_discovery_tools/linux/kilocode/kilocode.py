"""Kilo Code detection for Linux."""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)

# Match the trailing semver portion of a VS Code extension folder name,
# including pre-release suffixes like 1.2.3-pre.5 or 1.0.0-beta.1.
_VERSION_SUFFIX_RE = re.compile(r"-(\d+\.\d+\.\d+(?:[-+][\w.+-]+)?)$")


class LinuxKiloCodeDetector(BaseToolDetector):
    """Detector for Kilo Code installations on Linux systems."""

    SUPPORTED_IDES = ["Code", "Cursor"]
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"

    @property
    def tool_name(self) -> str:
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            result = self._check_user_for_kilocode(user_home)
            if result:
                return result
        return None

    def get_version(self) -> Optional[str]:
        """
        Delegate to detect() so the install-gating logic (extension settings
        dir present in any supported IDE's globalStorage) stays the single
        source of truth — a leftover ~/.vscode/extensions folder without a
        real install must not surface a version when detect() returns None.
        """
        result = self.detect()
        if result:
            version = result.get("version")
            return version if version != "Unknown" else None
        return None

    def _get_extension_version_for_user(self, user_home: Path, ide_name: str) -> Optional[str]:
        """
        Read the Kilo Code extension version for a single IDE.

        Scoped to one IDE so the version always matches the install_path
        reported by detect() — looking in another IDE's extensions dir would
        risk returning a leftover VS Code version against a Cursor install.

        Reads ``package.json`` inside the matching extension folder, falling
        back to the version suffix in the folder name if package.json is
        unreadable.
        """
        extensions_dir = user_home / ".vscode" / "extensions"
        if ide_name == "Cursor":
            extensions_dir = user_home / ".cursor" / "extensions"

        try:
            if not extensions_dir.exists():
                return None
            for ext_dir in extensions_dir.glob(f"{self.KILOCODE_EXTENSION_ID}-*"):
                package_json = ext_dir / "package.json"
                if package_json.exists():
                    try:
                        with open(package_json, "r", encoding="utf-8") as f:
                            version = json.load(f).get("version")
                        if version:
                            return version
                    except (json.JSONDecodeError, OSError):
                        pass
                m = _VERSION_SUFFIX_RE.search(ext_dir.name)
                if m:
                    return m.group(1)
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check extensions directory {extensions_dir}: {e}")
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        extension_path = None
        ide_with_extension = None
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_kilocode_extension(user_home, ide_name)
            if extension_path:
                ide_with_extension = ide_name
                break

        if not extension_path:
            return None

        return {
            "name": self.tool_name,
            "version": self._get_extension_version_for_user(user_home, ide_with_extension) or "Unknown",
            "install_path": str(extension_path),
        }

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        extension_dir = (
            user_home / ".config" / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID
        )
        try:
            if extension_dir.exists() and extension_dir.is_dir():
                logger.debug(f"Found Kilo Code extension directory for {ide_name} at: {extension_dir}")
                return extension_dir
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Kilo Code extension path for {ide_name}: {e}")
        return None
