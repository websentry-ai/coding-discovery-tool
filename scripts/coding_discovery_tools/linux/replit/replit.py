"""Replit detection for Linux."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


# Replit Desktop ships as a standard Electron bundle. Distro packages drop
# the resource tree at one of these paths; ``resources/app/package.json``
# inside any of them carries the version.
_SYSTEM_INSTALL_DIRS = (
    Path("/opt/Replit"),
    Path("/opt/replit"),
    Path("/usr/lib/replit"),
    Path("/usr/share/replit"),
)

_USER_INSTALL_RELATIVE_DIRS = (
    Path(".local/share/Replit"),
    Path(".local/share/replit"),
)


class LinuxReplitDetector(BaseToolDetector):
    """Detector for Replit installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit by requiring a real install resource tree, with the
        ``which replit`` lookup as a backstop.

        A genuine Replit Desktop install ships a ``resources/app/package.json``
        under one of the conventional install dirs. We gate on that resource
        tree rather than on the bare ``~/.config/Replit`` /
        ``~/.local/share/Replit`` dirs — those are residue config/data that
        survive uninstall and produced false positives. ``.local/share/Replit``
        is now only honoured as part of a resource-tree check (it is one of the
        candidate install dirs), not as a bare-dir-exists gate.
        """
        install_dir = self._find_install_dir()
        if install_dir:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(install_dir),
            }

        which_path = self._check_replit_command()
        if which_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": which_path,
            }

        return None

    def _find_install_dir(self) -> Optional[Path]:
        """
        Return the first candidate install dir that holds a real Replit
        resource tree (``resources/app/package.json``), which is removed on
        uninstall. Returns None if none qualify.
        """
        for install_dir in self._candidate_install_dirs():
            try:
                if not install_dir.is_dir():
                    continue
                if (install_dir / "resources" / "app" / "package.json").exists():
                    return install_dir
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check Replit install dir {install_dir}: {e}")
                continue
        return None

    def get_version(self) -> Optional[str]:
        """
        Read Replit Desktop's version.

        Replit Desktop is a standard Electron app — its ``resources/app/
        package.json`` carries the version. We scan the conventional system
        install paths plus per-user sideload locations. If nothing turns up,
        try ``replit --version`` as a last resort.

        Returns:
            Version string if discoverable, None otherwise.
        """
        for install_dir in self._candidate_install_dirs():
            try:
                if not install_dir.exists() or not install_dir.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            pkg_json = install_dir / "resources" / "app" / "package.json"
            version = self._read_version_file(pkg_json)
            if version:
                return version
        return self._version_via_command()

    def _candidate_install_dirs(self) -> List[Path]:
        dirs: List[Path] = list(_SYSTEM_INSTALL_DIRS)
        for user_home in get_linux_user_homes():
            for rel in _USER_INSTALL_RELATIVE_DIRS:
                dirs.append(user_home / rel)
        return dirs

    def _read_version_file(self, path: Path) -> Optional[str]:
        try:
            if not path.exists() or not path.is_file():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("version")
        except (json.JSONDecodeError, OSError, PermissionError) as e:
            logger.debug(f"Could not read Replit version file {path}: {e}")
        return None

    def _version_via_command(self) -> Optional[str]:
        try:
            output = run_command(["replit", "--version"], VERSION_TIMEOUT)
            if output:
                output = output.strip()
                if output:
                    return output
        except Exception as e:
            logger.debug(f"replit --version failed: {e}")
        return None

    def _check_replit_command(self) -> Optional[str]:
        try:
            output = run_command(["which", "replit"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    return path
        except Exception as e:
            logger.debug(f"Could not check for replit command: {e}")
        return None
