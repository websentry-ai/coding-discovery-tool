"""Replit detection for Linux."""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


# Replit Desktop ships via Electron Forge with the deb maker (package name
# ``replit``), which installs to ``/usr/lib/replit`` — the one official,
# verified system location. ``asar: true`` packs the app tree into
# ``resources/app.asar`` (the legacy ``resources/app/package.json`` is gone).
# The ``/opt/*`` and ``/usr/share/*`` entries are speculative (no official
# maker emits them) and dropped; ``/usr/lib/replit`` is authoritative.
_SYSTEM_INSTALL_DIRS = (
    Path("/usr/lib/replit"),
)

# Best-effort per-user sideload locations (no official maker emits them).
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
        Detect Replit by requiring a real install resource tree.

        A genuine Replit Desktop install ships either a packed
        ``resources/app.asar`` (Electron Forge ``asar: true``) or the legacy
        ``resources/app/package.json`` under one of the conventional install
        dirs. We gate on that resource tree rather than on the bare
        ``~/.config/Replit`` / ``~/.local/share/Replit`` dirs — those are
        residue config/data that survive uninstall and produced false
        positives. ``.local/share/Replit`` is now only honoured as part of a
        resource-tree check (it is one of the candidate install dirs), not as a
        bare-dir-exists gate.

        The ``which replit`` backstop was removed: the PyPI package ``replit``
        installs a ``replit`` console script, so the backstop name-collided and
        reported a phantom Replit Desktop for any Python dev who ran
        ``pip install replit``. The resource-tree gate is authoritative.
        """
        install_dir = self._find_install_dir()
        if install_dir:
            return {
                "name": self.tool_name,
                "version": self.get_version() or "Unknown",
                "install_path": str(install_dir),
            }

        return None

    def _find_install_dir(self) -> Optional[Path]:
        """
        Return the first candidate install dir that holds a real Replit
        resource tree — a packed ``resources/app.asar`` or the legacy
        ``resources/app/package.json`` — which is removed on uninstall. Returns
        None if none qualify.
        """
        for install_dir in self._candidate_install_dirs():
            try:
                if not install_dir.is_dir():
                    continue
                if self._has_resource_tree(install_dir):
                    return install_dir
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check Replit install dir {install_dir}: {e}")
                continue
        return None

    @staticmethod
    def _has_resource_tree(install_dir: Path) -> bool:
        """Return True iff ``install_dir`` holds a Replit Electron resource tree:
        a packed ``resources/app.asar`` (Forge ``asar: true``) or the legacy
        ``resources/app/package.json``. Never raises."""
        try:
            if (install_dir / "resources" / "app.asar").exists():
                return True
            if (install_dir / "resources" / "app" / "package.json").exists():
                return True
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not probe Replit resource tree in {install_dir}: {e}")
        return False

    def get_version(self) -> Optional[str]:
        """
        Read Replit Desktop's version.

        With Electron Forge ``asar: true`` the app tree is packed into
        ``resources/app.asar``, so the legacy ``resources/app/package.json`` is
        only present on older builds. We read it where it exists (scanning the
        conventional system install path plus per-user sideload locations).

        We deliberately do NOT parse the ``app.asar`` binary (zero-dep) and do
        NOT shell out to ``replit --version``: that name-collides with the PyPI
        ``replit`` package's console script and would report the PyPI version
        instead of the Desktop's. So an asar-only install yields None
        here and ``detect()`` reports the version as ``"Unknown"``.

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
        return None

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
