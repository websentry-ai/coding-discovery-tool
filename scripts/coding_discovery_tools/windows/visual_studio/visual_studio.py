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

A denied read is never reported as an absence: a clean absence is what lets the
backend prune a live install. Denials go through ``fail_if_anomalous``, which raises
only when the scan had any business reading the path (privileged, or the scanner's
own home) — raising unconditionally would mark every scan on every multi-user box
incomplete and nothing would ever be pruned.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# Substring, not exact: the documented id and the catalog id differ, and a miss here
# looks identical to Copilot being absent. Matches
# ``Component.VisualStudio.GitHub.Copilot`` without matching the unrelated
# ``Component.VisualStudio.GitHubCopilotForAzure.x64`` (no dot in that one).
_COPILOT_PACKAGE_MARKER = "github.copilot"

# Verified on VS 2022 17.14.37710.0. NOT ``Component.GitHub.Copilot``, which is what
# the enterprise-deploy doc names for ``setup.exe --add`` -- that id is absent from
# the catalog, so the installer accepts it, installs nothing, and exits 0.
_COPILOT_COMPONENT_ID = "Component.VisualStudio.GitHub.Copilot"

# Real state.json carries ``selectedPackages``; the published vswhere test fixture
# carries ``packages``. Read whichever is present rather than betting on one.
_COMPONENT_LIST_KEYS = ("selectedPackages", "packages")

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


def _read_state(state_file: Path) -> Tuple[Optional[Dict], str]:
    """``(state, outcome)`` for one ``state.json``.

    Four outcomes, not two: an instance dir with no state file is normal, but one
    we could not read and one holding garbage both leave that instance unknown,
    and an unknown instance must not be reported as an absent one.
    """
    try:
        with open(state_file, "r", encoding="utf-8-sig") as handle:
            state = json.load(handle)
        if isinstance(state, dict):
            return state, "ok"
        record_vs_probe("state_json", "malformed")
        return None, "malformed"
    except (FileNotFoundError, NotADirectoryError):
        return None, "absent"
    except (PermissionError, OSError) as exc:
        logger.debug("Could not read %s: %s", state_file, exc)
        record_vs_probe("state_json", "unreadable")
        return None, "unreadable"
    except (ValueError, UnicodeDecodeError) as exc:
        logger.debug("Malformed %s: %s", state_file, exc)
        record_vs_probe("state_json", "malformed")
        return None, "malformed"


def _display_name(state: Dict) -> str:
    """``"Visual Studio 2022 Enterprise"``, degrading to ``"Visual Studio"``.

    The documented state.json sample carries no ``displayName``, so the name is
    composed from ``product.id`` + version major. It is half of the install key, so a
    name that varies between runs would produce duplicate rows and prune thrash.

    ``Preview`` is part of the name because Release and Preview side by side is a
    mainstream setup, and the upload cache is keyed on the name without the install
    path — two same-named rows would overwrite each other's hash every run.
    """
    edition = _EDITIONS.get(_product_id(state))
    year = _RELEASE_YEARS.get((_version_tuple(state.get("installationVersion")) or (0,))[0])
    channel = "Preview" if str(state.get("channelId") or "").endswith(".Preview") else None
    return " ".join(part for part in (_FALLBACK_NAME, year, edition, channel) if part)


def _copilot_package(state: Dict) -> Optional[Dict]:
    """The Copilot component entry, or None when this instance has none.

    Returns the entry rather than its version: the vswhere path can prove Copilot is
    installed without being able to name a version, and ``None`` there must not read
    as "no Copilot".
    """
    for key in _COMPONENT_LIST_KEYS:
        packages = state.get(key)
        if not isinstance(packages, list):
            continue
        for package in packages:
            if not isinstance(package, dict):
                continue
            if _COPILOT_PACKAGE_MARKER in str(package.get("id") or "").lower():
                return package
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
    if not _sku_known(state):
        return False
    return _version_tuple(state.get("installationVersion")) >= _MIN_VERSION


def _product_id(state: Dict) -> str:
    """Lowercased ``product.id``, or "" when absent or the wrong shape.

    ``or {}`` covers null and missing but not a truthy non-dict, and a list here
    would AttributeError out of ``detect`` -- which marks the run incomplete and
    stops the backend pruning anything on the device.
    """
    product = state.get("product")
    if not isinstance(product, dict):
        return ""
    return str(product.get("id") or "").lower()


def _sku_known(state: Dict) -> bool:
    """Whether this is an IDE SKU we can name."""
    return _product_id(state) in _EDITIONS


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
            # `_listable_state` only pulled the first entry, so this is a second and
            # independent read that can be denied on its own. Swallowing it here
            # would report a clean absence while the probe above says "present".
            logger.debug("Could not list %s: %s", vs_dir, exc)
            record_vs_probe("user_config", "unreadable")
            fail_if_anomalous(self._user_home, f"Visual Studio user config dir unreadable: {vs_dir}")
        return None

    def _instance_states(self) -> Tuple[List[Dict], bool]:
        """``(states, unresolved)``.

        ``unresolved`` means some instance's state is unknown — denied, malformed,
        or answered by a vswhere that cannot see the Copilot component. It is NOT
        cleared by other instances parsing fine: only returned rows enter the
        manifest, so one readable instance alongside one inaccessible one would
        otherwise be reported as a complete inventory and the missing live install
        pruned.
        """
        instances_dir = self._instances_dir()
        if instances_dir is None:
            record_vs_probe("program_data", "absent")
            states, copilot_known = self._vswhere_states()
            return states, not copilot_known

        state = _listable_state(instances_dir)
        record_vs_probe("instances", state)
        if state != "present":
            states, copilot_known = self._vswhere_states()
            # A denied registry stays unresolved even when vswhere lists the IDEs:
            # vswhere has no flag that reports packages, so Copilot presence is
            # unknown and its row would be pruned as a clean absence.
            return states, (state == "unreadable" and not copilot_known)

        states = []
        unresolved = False
        try:
            for instance_dir in instances_dir.iterdir():
                if not instance_dir.is_dir():
                    continue
                parsed, outcome = _read_state(instance_dir / "state.json")
                if parsed:
                    states.append(parsed)
                elif outcome in ("unreadable", "malformed"):
                    # A dir with no state file is normal; one we could not read or
                    # could not parse leaves that instance unknown.
                    unresolved = True
        except (PermissionError, OSError) as exc:
            logger.debug("Could not enumerate %s: %s", instances_dir, exc)
            record_vs_probe("instances", "unreadable")
            states, copilot_known = self._vswhere_states()
            return states, not copilot_known
        # Empty is a real answer here; only fall back when the registry could not
        # be read, so "probed cleanly, found nothing" stays distinct from "denied".
        return states, unresolved

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

    def _vswhere_states(self) -> Tuple[List[Dict], bool]:
        """``(states, copilot_known)`` from the documented fallback API.

        vswhere reports no ``packages`` array and has no flag that produces one
        (``-requires`` filters, it does not report), so the component is asked for
        with a second, filtered call. When that call cannot answer, ``copilot_known``
        is False — silence from vswhere is not evidence Copilot is absent, and
        emitting IDE rows without a Copilot row would let an existing one be pruned.
        """
        rows = self._run_vswhere()
        if rows is None:
            return [], False
        record_vs_probe("vswhere", "present")

        with_copilot = self._run_vswhere("-requires", _COPILOT_COMPONENT_ID)
        if with_copilot is None:
            record_vs_probe("vswhere_copilot", "unknown")
            return [_from_vswhere(item) for item in rows], False

        copilot_paths = {
            item.get("installationPath") for item in with_copilot if item.get("installationPath")
        }
        record_vs_probe("vswhere_copilot", "present" if copilot_paths else "absent")

        states = []
        for item in rows:
            state = _from_vswhere(item)
            if item.get("installationPath") in copilot_paths:
                # No version: vswhere reports the IDE's, not the component's, and
                # stamping that would make the same machine answer differently
                # depending on which path ran. "Installed, version unknown" is true.
                state["selectedPackages"] = [{"id": _COPILOT_COMPONENT_ID, "version": None}]
            states.append(state)
        return states, True

    def detect(self) -> Optional[List[Dict]]:
        """IDE row per reportable instance, plus one Copilot row when the component
        is installed. Returns None when this user has no Visual Studio."""
        user_instance_dir = self._user_instance_dir()
        if user_instance_dir is None:
            return None

        states, unresolved = self._instance_states()
        rows: List[Dict] = []
        copilot_package = None
        for state in states:
            if not _is_reportable(state):
                # Distinct from a known SKU that is simply too old: only the
                # first means a product id we have never seen.
                record_vs_probe(
                    "instances", "below_min_version" if _sku_known(state) else "unknown_sku")
                continue
            install_path = state.get("installationPath")
            if not isinstance(install_path, str) or not install_path:
                # A falsy install_path turns the ownership gate off rather than
                # failing it (_install_in_another_users_home returns False), so an
                # unusable path must drop the row, not emit it with None.
                record_vs_probe("instances", "no_install_path")
                continue
            # Per instance, not per machine: Copilot is an optional component, so an
            # Enterprise install carrying it says nothing about the Community one
            # beside it, and the component version belongs to the instance that
            # supplied it. Mirrors how the JetBrains detector scopes plugins.
            package = _copilot_package(state)
            rows.append({
                "name": _display_name(state),
                "version": _version_text(state.get("installationVersion")),
                "install_path": install_path,
                "plugins": ["GitHub Copilot"] if package is not None else [],
            })
            if package is not None and copilot_package is None:
                copilot_package = package

        # Not gated on `rows`: only returned rows enter the manifest, so a readable
        # instance beside an unknown one would otherwise pass as a complete
        # inventory and the missing live install would be pruned.
        if unresolved:
            fail_if_anomalous(
                self._user_home, "Visual Studio instance state could not be resolved")

        if not rows:
            record_vs_probe("instances", "no_reportable")
            return None

        if copilot_package is not None:
            # Per-user install_path, not the machine-wide one: under a root scan the
            # Copilot row would otherwise fan out identically to every profile with
            # nothing to disown it.
            rows.append({
                "name": COPILOT_TOOL_NAME,
                "version": _version_text(copilot_package.get("version")),
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
