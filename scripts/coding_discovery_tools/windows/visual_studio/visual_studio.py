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
    fail_if_anomalous,
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

# Substring, not the exact id: the string as it appears in ``packages`` is
# unverified until a real device is seen, and a miss here looks identical to
# Copilot being absent.
_COPILOT_PACKAGE_MARKER = "github.copilot"

# What the deploy docs name for ``setup.exe --add`` and ``vswhere -requires``.
_COPILOT_COMPONENT_ID = "Component.GitHub.Copilot"

# A version string is capped before it becomes a row field: nothing in state.json
# has been seen in the wild, and the value reaches the backend unmodified.
_MAX_VERSION_LEN = 64

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

    ``Preview`` is part of the name because Release and Preview side by side is a
    mainstream setup, and the upload cache is keyed on the name without the install
    path — two same-named rows would overwrite each other's hash every run.
    """
    product = state.get("product") or {}
    product_id = str(product.get("id") or "").lower()
    edition = _EDITIONS.get(product_id)
    year = _RELEASE_YEARS.get((_version_tuple(state.get("installationVersion")) or (0,))[0])
    channel = "Preview" if str(state.get("channelId") or "").endswith(".Preview") else None
    return " ".join(part for part in (_FALLBACK_NAME, year, edition, channel) if part)


def _copilot_version(state: Dict) -> Optional[str]:
    """Version of the Copilot component in this instance, or None when absent."""
    packages = state.get("packages")
    if not isinstance(packages, list):
        return None
    for package in packages:
        if not isinstance(package, dict):
            continue
        if _COPILOT_PACKAGE_MARKER in str(package.get("id") or "").lower():
            return _version_text(package.get("version"))
    return None


def _version_text(value) -> Optional[str]:
    """A version safe to put on a row: a bounded string, or None.

    No real state.json has been read, so a nested object or a pathological string
    must not reach the backend verbatim.
    """
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text[:_MAX_VERSION_LEN] or None


def _is_reportable(state: Dict) -> bool:
    """An IDE SKU at 17.10+.

    Allow-list, not a Build Tools deny-list: 17.x also ships TestAgent,
    TestController, TeamExplorer and Server, all headless and none able to run
    Copilot. Keying on ``_EDITIONS`` also makes ``_display_name`` total.
    """
    product = state.get("product") or {}
    if str(product.get("id") or "").lower() not in _EDITIONS:
        return False
    return _version_tuple(state.get("installationVersion")) >= _MIN_VERSION


def _from_vswhere(item: Dict) -> Dict:
    """Reshape a vswhere row into the ``state.json`` shape the helpers expect.

    The two schemas differ: vswhere emits a flat ``productId`` where state.json
    nests ``product.id``. Left unmapped, every SKU check reads an empty id, so
    Build Tools passes the filter and the edition drops out of the row name.
    """
    return {**item, "product": {"id": item.get("productId")}}


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
        shared box. ``_listable_state``, not ``Path.exists()``: 3.14 reports a denied
        dir as absent, and a denial must not read as "this user does not use VS" —
        that is a clean absence, and a clean absence is what lets the backend prune
        a live install. ``fail_if_anomalous`` stops the scan only when the denial is
        anomalous (privileged, or the scanner's own home), as JetBrains and Claude
        Desktop already do for the same situation.
        """
        vs_dir = self._user_home / _USER_INSTANCE_TAIL
        state = _listable_state(vs_dir)
        record_vs_probe("user_config", state)
        if state == "unreadable":
            fail_if_anomalous(self._user_home, f"Visual Studio user config dir unreadable: {vs_dir}")
            return None
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
            return self._vswhere_states()
        # Empty is a real answer here; only fall back when the registry could not
        # be read, so "probed cleanly, found nothing" stays distinct from "denied".
        return states

    def _vswhere_exe(self) -> Optional[Path]:
        """``vswhere.exe``, or None when the Installer is not on this machine.

        Absolute path only — ``safe_exec_argv`` is a no-op on Windows, so a bare
        ``vswhere`` would resolve through PATH, and a PATH entry writable by a
        standard user is code execution as SYSTEM under an MDM scan.
        """
        for root in windows_program_files_roots():
            vswhere = root / _VSWHERE_TAIL
            if vswhere.is_file():
                return vswhere
        return None

    def _run_vswhere(self, *extra_args: str) -> Optional[List[Dict]]:
        """vswhere rows, or None when it could not answer. Never raises."""
        vswhere = self._vswhere_exe()
        if vswhere is None:
            record_vs_probe("vswhere", "absent")
            return None
        output, ran = run_command_status(
            [str(vswhere), "-products", "*", "-all", "-prerelease",
             "-format", "json", "-utf8", "-nologo", *extra_args]
        )
        if not ran:
            record_vs_probe("vswhere", "unreadable")
            return None
        try:
            parsed = json.loads(output or "[]")
        except ValueError:
            record_vs_probe("vswhere", "malformed")
            return None
        return [item for item in parsed if isinstance(item, dict)]

    def _vswhere_states(self) -> List[Dict]:
        """Instances from the documented fallback API, in ``state.json`` shape.

        vswhere reports no ``packages`` array and has no flag that produces one
        (``-requires`` filters, it does not report), so the Copilot component is
        asked for with a second, filtered call rather than inferred from silence.
        """
        rows = self._run_vswhere()
        if rows is None:
            return []
        record_vs_probe("vswhere", "present")

        with_copilot = self._run_vswhere("-requires", _COPILOT_COMPONENT_ID)
        if with_copilot is None:
            # Absence unproven: leave the marker off rather than assert "no Copilot".
            record_vs_probe("vswhere_copilot", "unknown")
            copilot_paths = set()
        else:
            copilot_paths = {
                item.get("installationPath") for item in with_copilot if item.get("installationPath")
            }
            record_vs_probe("vswhere_copilot", "present" if copilot_paths else "absent")

        states = []
        for item in rows:
            state = _from_vswhere(item)
            if item.get("installationPath") in copilot_paths:
                state["packages"] = [{"id": _COPILOT_COMPONENT_ID,
                                      "version": item.get("installationVersion")}]
            states.append(state)
        return states

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
            install_path = state.get("installationPath")
            if not isinstance(install_path, str) or not install_path:
                # A falsy install_path turns the ownership gate off rather than
                # failing it (_install_in_another_users_home returns False), so an
                # unusable path must drop the row, not emit it with None.
                record_vs_probe("instances", "no_install_path")
                continue
            rows.append({
                "name": _display_name(state),
                "version": _version_text(state.get("installationVersion")),
                "install_path": install_path,
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
