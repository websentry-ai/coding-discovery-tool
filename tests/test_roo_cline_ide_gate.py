"""IDE-install gating tests for Roo Code & Cline on macOS.

Two fixes are under test:

1. The globalStorage main loop changed from ``ide_installed OR extension_path``
   to ``ide_installed AND extension_path``. A stale
   ``.../<IDE>/User/globalStorage/<ext-id>`` dir survives an IDE uninstall, so
   the OR form surfaced a phantom row for an IDE that is no longer installed.

2. The Antigravity branch is now gated on
   ``_check_ide_installation("Antigravity")``. ``~/.antigravity/extensions``
   (which holds ``extensions.json``) survives uninstall, so the extensions.json
   entry alone is not proof of install.

Both directions are proven per fix: real (.app present) -> detected; residue
(globalStorage / extensions.json only, no .app) -> not in results.

The tests build a real tmp ``/Applications`` tree and point the detector's
``APPLICATIONS_DIR`` at it, so the actual ``_check_ide_installation`` code runs
(rather than being mocked away).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.windows_extraction_helpers as win_helpers
import scripts.coding_discovery_tools.linux_extraction_helpers as linux_helpers
from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
from scripts.coding_discovery_tools.macos.roo_code.roo_code import MacOSRooDetector
from scripts.coding_discovery_tools.windows.cline.cline import WindowsClineDetector
from scripts.coding_discovery_tools.windows.roo_code.roo_code import WindowsRooDetector
from scripts.coding_discovery_tools.linux.cline.cline import LinuxClineDetector
from scripts.coding_discovery_tools.linux.roo_code.roo_code import LinuxRooDetector

ROO_EXT_ID = "rooveterinaryinc.roo-cline"
CLINE_EXT_ID = "saoudrizwan.claude-dev"


class _IdeGateMixin:
    """Shared assertions for the two detectors. Mixed with ``TestCase`` by the
    concrete subclasses (so the mixin itself is never collected/run)."""

    Detector = None
    ext_id = None
    tool_label = None  # e.g. "Roo Code"
    per_user_method = None  # e.g. "_detect_roo_for_user"

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.apps = self.root / "Applications"
        self.apps.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    # --- fixture builders ----------------------------------------------

    def _make_globalstorage(self, ide_folder: str) -> Path:
        gs = (self.home / "Library" / "Application Support" / ide_folder
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def _make_app(self, app_name: str) -> Path:
        app = self.apps / app_name
        app.mkdir(parents=True, exist_ok=True)
        return app

    def _make_antigravity_extension_json(self, version: str = "3.1.0") -> Path:
        ext_dir = self.home / ".antigravity" / "extensions"
        ext_loc = ext_dir / f"{self.ext_id}-{version}"
        ext_loc.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text(json.dumps([
            {
                "identifier": {"id": self.ext_id},
                "version": version,
                "relativeLocation": f"{self.ext_id}-{version}",
            }
        ]), encoding="utf-8")
        return ext_loc

    def _detect(self):
        """Drive the per-user detection with APPLICATIONS_DIR pointed at the
        tmp /Applications tree, so the real ``_check_ide_installation`` runs."""
        with patch.object(self.Detector, "APPLICATIONS_DIR", self.apps):
            det = self.Detector()
            return getattr(det, self.per_user_method)(self.home)

    def _names(self, results):
        return [r["name"] for r in results]

    # --- globalStorage OR->AND fix -------------------------------------

    def test_globalstorage_plus_app_detected(self):
        """globalStorage present AND VS Code.app present -> detected."""
        gs = self._make_globalstorage("Code")
        self._make_app("Visual Studio Code.app")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VS Code)")
        self.assertEqual(row["install_path"], str(gs))

    def test_globalstorage_without_app_not_detected(self):
        """globalStorage present but NO .app for that IDE -> [] (the OR->AND
        fix). Residue case: AppSupport/globalStorage survived the IDE uninstall."""
        self._make_globalstorage("Code")
        results = self._detect()
        self.assertEqual(
            results, [],
            "Stale globalStorage from an uninstalled IDE must not surface a row",
        )

    # --- Antigravity branch gate ---------------------------------------

    def test_antigravity_extension_without_app_not_detected(self):
        """extensions.json lists the ext but no Antigravity.app -> not in
        results (residue ~/.antigravity/extensions survived uninstall)."""
        self._make_antigravity_extension_json()
        results = self._detect()
        self.assertNotIn(
            f"{self.tool_label} (Antigravity)", self._names(results),
            "Antigravity extensions.json alone (no .app) must not surface a row",
        )

    def test_antigravity_extension_with_app_detected(self):
        """extensions.json lists the ext AND Antigravity.app present -> detected."""
        ext_loc = self._make_antigravity_extension_json()
        self._make_app("Antigravity.app")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (Antigravity)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (Antigravity)")
        self.assertEqual(row["install_path"], str(ext_loc))

    def test_nothing_installed_returns_empty(self):
        """No globalStorage, no .app, no extensions.json -> []."""
        self.assertEqual(self._detect(), [])

    # --- user-local ~/Applications (false-negative guard) ----------------

    def test_globalstorage_plus_user_applications_app_detected(self):
        """FIX #2 (macOS hardening): globalStorage present AND the host editor
        installed in the USER-LOCAL ``~/Applications`` (not ``/Applications``)
        -> detected. Guards the false negative where the editor was drag-
        installed into the home folder.

        Fails against the pre-fix ``_check_ide_installation`` that only looked
        in ``/Applications``."""
        gs = self._make_globalstorage("Code")
        user_apps = self.home / "Applications"
        user_apps.mkdir(parents=True, exist_ok=True)
        (user_apps / "Visual Studio Code.app").mkdir(parents=True, exist_ok=True)
        # ``/Applications`` (self.apps) stays EMPTY, so only ~/Applications can
        # satisfy the gate.
        results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VS Code)")
        self.assertEqual(row["install_path"], str(gs))


class TestRooIdeGate(_IdeGateMixin, unittest.TestCase):
    Detector = MacOSRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class TestClineIdeGate(_IdeGateMixin, unittest.TestCase):
    Detector = MacOSClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


# =====================================================================
# FIX #2 — Windows + Linux host-IDE gate (was deferred I1)
#
# Win/Linux Cline/Roo previously emitted a row on the bare
# ``globalStorage/<ext-id>`` dir with NO host check — that dir survives an
# editor uninstall, so it was a residue false positive. The fix gates the
# main-row loop on ``host_installed AND extension`` (mirrors macOS), using a
# THOROUGH host probe so a real user is never hidden (false-negative guard).
# =====================================================================


class _WinLinuxIdeGateMixin:
    """End-to-end gate assertions for a (Cline|Roo) detector on Win/Linux:
    residue-only globalStorage -> NOT detected (FP kill); globalStorage + one
    host install -> detected (false-negative guard). Concrete subclasses set
    the detector, the per-user method, the ext id, the label, and the two
    fixture builders (globalStorage + host install)."""

    Detector = None
    ext_id = None
    tool_label = None
    per_user_method = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    # --- per-OS fixture builders (overridden by subclasses) --------------

    def _make_globalstorage(self, ide_folder: str) -> Path:  # pragma: no cover
        raise NotImplementedError

    def _make_host_install(self, ide_folder: str) -> None:  # pragma: no cover
        raise NotImplementedError

    # --- shared driver / assertions --------------------------------------

    def _detect(self):
        det = self.Detector()
        return getattr(det, self.per_user_method)(self.home)

    def _names(self, results):
        return [r["name"] for r in results]

    def test_globalstorage_residue_without_host_not_detected(self):
        """globalStorage present but the host editor absent EVERYWHERE -> []
        (the residue FP this fix kills). ``shutil.which`` is neutralised and no
        host artifact is created, so nothing can satisfy the gate."""
        self._make_globalstorage("Code")
        with patch("shutil.which", return_value=None):
            results = self._detect()
        self.assertEqual(
            results, [],
            "Residue globalStorage with no host editor must not surface a row",
        )

    def test_globalstorage_plus_host_detected(self):
        """globalStorage present AND the host editor installed (per-user
        Programs on Windows / ~/.local/share on Linux) -> detected (the
        false-negative guard)."""
        gs = self._make_globalstorage("Code")
        self._make_host_install("Code")
        with patch("shutil.which", return_value=None):
            results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VS Code)")
        self.assertEqual(row["install_path"], str(gs))


class _WindowsIdeGateMixin(_WinLinuxIdeGateMixin):
    def _make_globalstorage(self, ide_folder: str) -> Path:
        gs = (self.home / "AppData" / "Roaming" / ide_folder
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def _make_host_install(self, ide_folder: str) -> None:
        # Per-user %LOCALAPPDATA%\Programs\Microsoft VS Code (Programs class).
        dir_name = win_helpers._WINDOWS_IDE_INSTALL_INFO[ide_folder]["dir_names"][0]
        install = self.home / "AppData" / "Local" / "Programs" / dir_name
        install.mkdir(parents=True, exist_ok=True)


class TestWindowsClineIdeGate(_WindowsIdeGateMixin, unittest.TestCase):
    Detector = WindowsClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


class TestWindowsRooIdeGate(_WindowsIdeGateMixin, unittest.TestCase):
    Detector = WindowsRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class _LinuxIdeGateMixin(_WinLinuxIdeGateMixin):
    def _make_globalstorage(self, ide_folder: str) -> Path:
        gs = (self.home / ".config" / ide_folder
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def _make_host_install(self, ide_folder: str) -> None:
        # Per-user ~/.local/share/<name> sideload (the ~/.local class).
        name = linux_helpers._LINUX_IDE_INSTALL_INFO[ide_folder]["opt_local_names"][0]
        install = self.home / ".local" / "share" / name
        install.mkdir(parents=True, exist_ok=True)


class TestLinuxClineIdeGate(_LinuxIdeGateMixin, unittest.TestCase):
    Detector = LinuxClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


class TestLinuxRooIdeGate(_LinuxIdeGateMixin, unittest.TestCase):
    Detector = LinuxRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class TestWindowsIdeProbeLocationClasses(unittest.TestCase):
    """Each Windows host-install LOCATION CLASS must INDEPENDENTLY satisfy the
    gate (false-negative guard): per-user Programs, machine-wide Program Files,
    and the launcher on PATH. Drives the shared ``is_windows_ide_installed``
    helper that the detectors delegate to."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_absent_everywhere_false(self):
        """No install anywhere + ``which`` empty -> (False, None)."""
        with patch("shutil.which", return_value=None):
            installed, _ = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertFalse(installed)

    def test_per_user_programs_detected(self):
        """``%LOCALAPPDATA%\\Programs\\Cursor`` (per-user) -> installed."""
        install = self.home / "AppData" / "Local" / "Programs" / "Cursor"
        install.mkdir(parents=True, exist_ok=True)
        with patch("shutil.which", return_value=None):
            installed, path = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_program_files_detected(self):
        """Machine-wide ``C:\\Program Files\\Cursor`` -> installed. Patches the
        ``Path`` constructor inside the helper module so the hardcoded
        ``C:\\Program Files`` resolves to a tmp dir."""
        prog_files = Path(self.tmp.name) / "ProgramFiles"
        install = prog_files / "Cursor"
        install.mkdir(parents=True, exist_ok=True)

        real_path_cls = win_helpers.Path

        def fake_path(arg):
            if arg == "C:\\Program Files":
                return prog_files
            return real_path_cls(arg)

        with patch.object(win_helpers, "Path", side_effect=fake_path), \
             patch("shutil.which", return_value=None):
            installed, path = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_path_launcher_detected(self):
        """The editor launcher on PATH (``shutil.which`` resolves) -> installed,
        even with no install dir present."""
        with patch("shutil.which", side_effect=lambda n: r"C:\X\cursor.exe" if n == "cursor.exe" else None):
            installed, path = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, r"C:\X\cursor.exe")


class TestLinuxIdeProbeLocationClasses(unittest.TestCase):
    """Each Linux host-install LOCATION CLASS must INDEPENDENTLY satisfy the
    gate (false-negative guard): system ``/usr/share``, ``/opt``,
    ``~/.local/share``, Snap, Flatpak, and the binary on PATH. Drives the shared
    ``is_linux_ide_installed`` helper that the detectors delegate to.

    The absolute-path classes (system/opt/snap/flatpak) are made hermetic by
    redirecting the helper's absolute bases to a tmp tree via a ``Path`` shim."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _abs_redirect(self):
        """Patch ``Path`` inside the helper so absolute system roots resolve
        under the tmp tree. ``shutil.which`` is left to the caller."""
        real_path_cls = linux_helpers.Path
        root = self.root

        def fake_path(arg):
            s = str(arg)
            if s.startswith("/") and not s.startswith(str(root)):
                # Strip the leading slash and re-root under tmp.
                return root / s.lstrip("/")
            return real_path_cls(arg)

        return patch.object(linux_helpers, "Path", side_effect=fake_path)

    def test_absent_everywhere_false(self):
        with self._abs_redirect(), patch("shutil.which", return_value=None):
            installed, _ = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertFalse(installed)

    def test_system_dir_detected(self):
        """``/usr/share/code`` -> installed."""
        install = self.root / "usr" / "share" / "code"
        install.mkdir(parents=True, exist_ok=True)
        with self._abs_redirect(), patch("shutil.which", return_value=None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_opt_dir_detected(self):
        """``/opt/VSCode`` -> installed."""
        install = self.root / "opt" / "VSCode"
        install.mkdir(parents=True, exist_ok=True)
        with self._abs_redirect(), patch("shutil.which", return_value=None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_user_local_share_detected(self):
        """``~/.local/share/VSCode`` (per-user sideload) -> installed. This is a
        user_home-relative path, so no ``Path`` redirect is needed."""
        install = self.home / ".local" / "share" / "VSCode"
        install.mkdir(parents=True, exist_ok=True)
        with patch("shutil.which", return_value=None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_snap_detected(self):
        """``/snap/code`` -> installed."""
        install = self.root / "snap" / "code"
        install.mkdir(parents=True, exist_ok=True)
        with self._abs_redirect(), patch("shutil.which", return_value=None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_flatpak_detected(self):
        """``/var/lib/flatpak/app/com.visualstudio.code`` -> installed. The
        system flatpak root is a module-level constant, so we patch it directly
        to a tmp dir (the ``Path`` shim only catches inline constructions)."""
        flatpak_root = self.root / "var" / "lib" / "flatpak"
        install = flatpak_root / "app" / "com.visualstudio.code"
        install.mkdir(parents=True, exist_ok=True)
        with patch.object(linux_helpers, "_FLATPAK_SYSTEM_ROOT", flatpak_root), \
             self._abs_redirect(), patch("shutil.which", return_value=None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, str(install))

    def test_path_binary_detected(self):
        """The editor binary on PATH (``shutil.which`` resolves) -> installed."""
        with self._abs_redirect(), \
             patch("shutil.which", side_effect=lambda n: "/usr/bin/code" if n == "code" else None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, "/usr/bin/code")


if __name__ == "__main__":
    unittest.main()
