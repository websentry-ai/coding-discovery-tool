r"""
Visual Studio + GitHub Copilot-in-Visual-Studio detection for Windows.

Visual Studio installs machine-wide and records every instance under
``%ProgramData%\Microsoft\VisualStudio\Packages\_Instances\<id>\state.json``
(learn.microsoft.com/visualstudio/install/tools-for-managing-visual-studio-instances).
Copilot ships as an Installer *component*, not a marketplace extension, so its
presence is an entry in that file's ``packages`` list rather than a folder on disk.

Only 17.10+ is reported: Copilot first shipped bundled there, so an older instance
can never carry the component. Build Tools instances are skipped — no IDE, no Copilot.

``vswhere.exe`` answers the same question and is the documented API, but it is a
subprocess; it runs only when ``_Instances`` itself is unreadable.

Never raises. A denied path is recorded as a probe and falls through: one raising
detector marks the whole scan incomplete and disables pruning for every tool on the
device (tests/test_scan_completed_manifest.py:682).
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import (
    _listable_state,
    dir_state,
    record_vs_probe,
    run_command_status,
    windows_program_files_roots,
)

logger = logging.getLogger(__name__)

# Machine-wide instance registry. ProgramData is not in windows_program_files_roots().
_INSTANCES_TAIL = Path("Microsoft") / "VisualStudio" / "Packages" / "_Instances"

# Per-user instance config dir. Its presence is what ties a machine-wide install to
# a specific user, mirroring the VS Code gate in github_copilot/detect_copilot.py.
_USER_INSTANCE_TAIL = Path("AppData") / "Local" / "Microsoft" / "VisualStudio"

_VSWHERE_TAIL = Path("Microsoft Visual Studio") / "Installer" / "vswhere.exe"

# Copilot became a bundled Installer component in 17.10; nothing older can carry it.
_MIN_VERSION = (17, 10)

_BUILD_TOOLS_PRODUCT = "microsoft.visualstudio.product.buildtools"

# Substring, not the exact ``Component.GitHub.Copilot`` the deploy docs name for
# ``setup.exe --add``: the id as it appears in ``packages`` is unverified until a
# real device is seen, and a miss here looks identical to Copilot being absent.
_COPILOT_PACKAGE_MARKER = "github.copilot"

_EDITIONS = {
    "microsoft.visualstudio.product.enterprise": "Enterprise",
    "microsoft.visualstudio.product.professional": "Professional",
    "microsoft.visualstudio.product.community": "Community",
}

# Version major -> release year, for the display name.
_RELEASE_YEARS = {17: "2022", 18: "2026"}

_FALLBACK_NAME = "Visual Studio"

COPILOT_TOOL_NAME = "GitHub Copilot (Visual Studio)"


def _version_tuple(version: str) -> tuple:
    """``"17.14.3"`` -> ``(17, 14, 3)``. Malformed yields ``()``, which sorts earliest.

    Tuple, not string: ``"17.9" > "17.10"`` lexically, which would silently drop the
    entire 17.10-17.99 population this detector exists to find.
    """
    try:
        return tuple(int(p) for p in str(version).split(".") if p.isdigit())
    except (AttributeError, TypeError, ValueError):
        return ()


def _read_state(state_file: Path) -> Optional[Dict]:
    """Parse one ``state.json``. Returns None for absent, denied or malformed."""
    try:
        with open(state_file, "r", encoding="utf-8-sig") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else None
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (PermissionError, OSError) as exc:
        logger.debug("Could not read %s: %s", state_file, exc)
        record_vs_probe("state_json", "unreadable")
        return None
    except (ValueError, UnicodeDecodeError) as exc:
        logger.debug("Malformed %s: %s", state_file, exc)
        record_vs_probe("state_json", "malformed")
        return None


def _display_name(state: Dict) -> str:
    """``"Visual Studio 2022 Enterprise"``, degrading to ``"Visual Studio"``.

    The documented state.json sample carries no ``displayName``, so the name is
    composed from ``product.id`` + version major. It is half of the install key, so a
    name that varies between runs would produce duplicate rows and prune thrash.
    """
    product = state.get("product") or {}
    product_id = str(product.get("id") or "").lower()
    edition = _EDITIONS.get(product_id)
    year = _RELEASE_YEARS.get((_version_tuple(state.get("installationVersion")) or (0,))[0])
    return " ".join(part for part in (_FALLBACK_NAME, year, edition) if part)


def _copilot_version(state: Dict) -> Optional[str]:
    """Version of the Copilot component in this instance, or None when absent."""
    packages = state.get("packages")
    if not isinstance(packages, list):
        return None
    for package in packages:
        if not isinstance(package, dict):
            continue
        if _COPILOT_PACKAGE_MARKER in str(package.get("id") or "").lower():
            return package.get("version") or None
    return None


def _is_reportable(state: Dict) -> bool:
    """17.10+ and not Build Tools."""
    product = state.get("product") or {}
    if str(product.get("id") or "").lower() == _BUILD_TOOLS_PRODUCT:
        return False
    return _version_tuple(state.get("installationVersion")) >= _MIN_VERSION


class WindowsVisualStudioDetector(BaseToolDetector):
    """Visual Studio IDE + Copilot component detector for Windows."""

    @property
    def tool_name(self) -> str:
        return _FALLBACK_NAME

    @property
    def _user_home(self) -> Path:
        return Path(getattr(self, "user_home", None) or Path.home())

    def _instances_dir(self) -> Optional[Path]:
        """``%ProgramData%\\Microsoft\\VisualStudio\\Packages\\_Instances``."""
        program_data = os.environ.get("ProgramData")
        return Path(program_data) / _INSTANCES_TAIL if program_data else None

    def _user_instance_dir(self) -> Optional[Path]:
        """The user's own VS config dir, or None when they have never run VS.

        A machine-wide install would otherwise be attributed to every profile on a
        shared box. ``dir_state``, not ``Path.exists()``: 3.14 reports a denied dir
        as absent, which would disown a user whose config we simply cannot read.
        """
        vs_dir = self._user_home / _USER_INSTANCE_TAIL
        state = _listable_state(vs_dir)
        record_vs_probe("user_config", state)
        if state != "present":
            return None
        try:
            for entry in vs_dir.iterdir():
                if entry.is_dir() and entry.name[:1].isdigit():
                    return entry
        except (PermissionError, OSError) as exc:
            logger.debug("Could not list %s: %s", vs_dir, exc)
        return None

    def _instance_states(self) -> List[Dict]:
        """Every parsed ``state.json``, via ``_Instances`` or the vswhere fallback."""
        instances_dir = self._instances_dir()
        if instances_dir is None:
            record_vs_probe("program_data", "absent")
            return self._vswhere_states()

        state = _listable_state(instances_dir)
        record_vs_probe("instances", state)
        if state != "present":
            return self._vswhere_states()

        states = []
        try:
            for instance_dir in instances_dir.iterdir():
                if not instance_dir.is_dir():
                    continue
                parsed = _read_state(instance_dir / "state.json")
                if parsed:
                    states.append(parsed)
        except (PermissionError, OSError) as exc:
            logger.debug("Could not enumerate %s: %s", instances_dir, exc)
            record_vs_probe("instances", "unreadable")
        return states or self._vswhere_states()

    def _vswhere_states(self) -> List[Dict]:
        """Documented fallback. Absolute path only — ``safe_exec_argv`` is a no-op on
        Windows, so a bare ``vswhere`` would resolve through PATH and a user-writable
        PATH entry is code execution as SYSTEM under an MDM scan."""
        for root in windows_program_files_roots():
            vswhere = root / _VSWHERE_TAIL
            if dir_state(vswhere.parent) != "present":
                continue
            output, ran = run_command_status(
                [str(vswhere), "-products", "*", "-all", "-prerelease",
                 "-format", "json", "-utf8", "-nologo"]
            )
            if not ran:
                record_vs_probe("vswhere", "unreadable")
                continue
            try:
                parsed = json.loads(output or "[]")
            except ValueError:
                record_vs_probe("vswhere", "malformed")
                continue
            record_vs_probe("vswhere", "present")
            return [item for item in parsed if isinstance(item, dict)]
        record_vs_probe("vswhere", "absent")
        return []

    def detect(self) -> Optional[List[Dict]]:
        """IDE row per reportable instance, plus one Copilot row when the component
        is installed. Returns None when this user has no Visual Studio."""
        user_instance_dir = self._user_instance_dir()
        if user_instance_dir is None:
            return None

        rows: List[Dict] = []
        copilot_version = None
        for state in self._instance_states():
            if not _is_reportable(state):
                continue
            rows.append({
                "name": _display_name(state),
                "version": state.get("installationVersion"),
                "install_path": state.get("installationPath"),
                "plugins": [],
            })
            copilot_version = copilot_version or _copilot_version(state)

        if not rows:
            record_vs_probe("instances", "no_reportable")
            return None

        if copilot_version is not None:
            for row in rows:
                row["plugins"] = ["GitHub Copilot"]
            # Per-user install_path, not the machine-wide one: under a root scan the
            # Copilot row would otherwise fan out identically to every profile with
            # nothing to disown it.
            rows.append({
                "name": COPILOT_TOOL_NAME,
                "version": copilot_version,
                "install_path": str(user_instance_dir),
            })
        return rows

    def get_version(self) -> Optional[str]:
        """Comma-separated ``<name> <version>`` for each detected instance."""
        rows = self.detect() or []
        versions = [
            f"{row['name']} {row['version']}"
            for row in rows
            if row.get("name") != COPILOT_TOOL_NAME and row.get("version")
        ]
        return ", ".join(versions) or None
