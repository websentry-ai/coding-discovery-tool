"""extensions.json-entry gating tests for Roo Code & Cline (macOS/Windows/Linux).

The detection gate keys on the editor's ``extensions.json`` registry, not the
extension's ``globalStorage/<ext-id>`` dir (which survives uninstall —
microsoft/vscode#119022 — and produced phantom rows). Both directions are proven:
a live entry -> detected (version + extensions dir as install_path); globalStorage
residue with NO entry -> not in results (the FP kill). Antigravity keeps its own
install gate but reads its entry through the same registry helper.

``find_extension_in_editor`` runs unmocked, so these are true end-to-end detector
tests over hermetic tmp homes. The host-IDE probe helpers are no longer called by
the detectors but stay covered by ``TestWindows/LinuxIdeProbeLocationClasses``.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.windows_extraction_helpers as win_helpers
import scripts.coding_discovery_tools.linux_extraction_helpers as linux_helpers
from scripts.coding_discovery_tools.vscode_extension_helpers import extensions_dir_for_editor
from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
from scripts.coding_discovery_tools.macos.roo_code.roo_code import MacOSRooDetector
from scripts.coding_discovery_tools.windows.cline.cline import WindowsClineDetector
from scripts.coding_discovery_tools.windows.roo_code.roo_code import WindowsRooDetector
from scripts.coding_discovery_tools.linux.cline.cline import LinuxClineDetector
from scripts.coding_discovery_tools.linux.roo_code.roo_code import LinuxRooDetector

ROO_EXT_ID = "rooveterinaryinc.roo-cline"
CLINE_EXT_ID = "saoudrizwan.claude-dev"


class _ExtensionsGateMixin:
    """Shared end-to-end assertions for a (Cline|Roo) detector. The registry entry
    is the gate; globalStorage residue must NOT detect. Concrete subclasses set
    the detector, the ext id, the label, the per-user method, and the per-OS
    globalStorage builder."""

    Detector = None
    ext_id = None
    tool_label = None  # e.g. "Roo Code"
    per_user_method = None  # e.g. "_detect_roo_for_user"

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    # --- fixture builders ----------------------------------------------

    def _make_registry_entry(self, ide_key: str, version: str = "3.1.0") -> Path:
        """Write an extensions.json entry for ``ide_key`` and return the
        extensions dir (the detector's install_path)."""
        ext_dir = extensions_dir_for_editor(self.home, ide_key)
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text(json.dumps([
            {
                "identifier": {"id": self.ext_id},
                "version": version,
                "relativeLocation": f"{self.ext_id}-{version}",
            }
        ]), encoding="utf-8")
        return ext_dir

    def _make_globalstorage(self, ide_key: str) -> Path:  # pragma: no cover
        raise NotImplementedError

    def _detect(self):
        det = self.Detector()
        return getattr(det, self.per_user_method)(self.home)

    def _names(self, results):
        return [r["name"] for r in results]

    # --- registry entry present -> detected ----------------------------

    def test_registry_entry_detected(self):
        """A live extensions.json entry -> detected; install_path is the
        extensions dir; version comes from the entry."""
        ext_dir = self._make_registry_entry("Code", version="3.1.0")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VS Code)")
        self.assertEqual(row["install_path"], str(ext_dir))
        self.assertEqual(row["version"], "3.1.0")

    # --- globalStorage residue WITHOUT a registry entry -> NOT detected -

    def test_globalstorage_residue_without_extensions_entry_not_detected(self):
        """The core FP kill: globalStorage residue (survives uninstall) present but
        NO extensions.json entry -> no row."""
        self._make_globalstorage("Code")
        results = self._detect()
        self.assertEqual(
            results, [],
            "globalStorage residue with no extensions.json entry must not surface a row",
        )

    def test_nothing_present_returns_empty(self):
        """No registry entry, no globalStorage -> []."""
        self.assertEqual(self._detect(), [])


# =====================================================================
# macOS — also covers the Antigravity branch (keeps its own .app gate)
# =====================================================================


class _MacOSExtensionsGateMixin(_ExtensionsGateMixin):
    def setUp(self):
        super().setUp()
        self.apps = Path(self.tmp.name) / "Applications"
        self.apps.mkdir()

    def _make_globalstorage(self, ide_key: str) -> Path:
        gs = (self.home / "Library" / "Application Support" / ide_key
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def _make_app(self, app_name: str) -> Path:
        app = self.apps / app_name
        app.mkdir(parents=True, exist_ok=True)
        return app

    def _detect(self):
        # APPLICATIONS_DIR points at the tmp tree so the real Antigravity .app
        # gate runs (Antigravity is still install-gated).
        with patch.object(self.Detector, "APPLICATIONS_DIR", self.apps):
            det = self.Detector()
            return getattr(det, self.per_user_method)(self.home)

    def test_vscodium_registry_entry_detected_for_roo(self):
        """Roo Code added VSCodium to SUPPORTED_IDES — a VSCodium registry entry
        is detected. (Cline does not list VSCodium; this is overridden there.)"""
        ext_dir = self._make_registry_entry("VSCodium", version="2.0.0")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (VSCodium)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VSCodium)")
        self.assertEqual(row["install_path"], str(ext_dir))

    # --- Antigravity branch: registry entry + .app gate ----------------

    def test_antigravity_entry_without_app_not_detected(self):
        """Antigravity extensions.json entry but no Antigravity.app -> not in
        results (Antigravity keeps its own install gate)."""
        self._make_registry_entry("Antigravity")
        results = self._detect()
        self.assertNotIn(f"{self.tool_label} (Antigravity)", self._names(results))

    def test_antigravity_entry_with_app_detected(self):
        """Antigravity extensions.json entry AND Antigravity.app -> detected;
        install_path is the Antigravity extensions dir."""
        ext_dir = self._make_registry_entry("Antigravity")
        self._make_app("Antigravity.app")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (Antigravity)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (Antigravity)")
        self.assertEqual(row["install_path"], str(ext_dir))


class TestRooIdeGate(_MacOSExtensionsGateMixin, unittest.TestCase):
    Detector = MacOSRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class TestClineIdeGate(_MacOSExtensionsGateMixin, unittest.TestCase):
    Detector = MacOSClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"

    def test_vscodium_registry_entry_detected_for_roo(self):
        """Cline does NOT list VSCodium; a VSCodium-only entry yields no row."""
        self._make_registry_entry("VSCodium", version="2.0.0")
        self.assertEqual(self._detect(), [])


# =====================================================================
# Windows + Linux — no host-IDE gate anymore; the registry entry is the gate
# =====================================================================


class _WindowsExtensionsGateMixin(_ExtensionsGateMixin):
    def _make_globalstorage(self, ide_key: str) -> Path:
        gs = (self.home / "AppData" / "Roaming" / ide_key
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def test_host_install_not_required(self):
        """Regression for the dropped host-IDE AND-gate: a registry entry with NO
        host editor installed anywhere (``shutil.which`` neutralised) still detects
        — the entry alone is proof of a live install."""
        self._make_registry_entry("Code")
        with patch("shutil.which", return_value=None):
            results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))


class TestWindowsClineIdeGate(_WindowsExtensionsGateMixin, unittest.TestCase):
    Detector = WindowsClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


class TestWindowsRooIdeGate(_WindowsExtensionsGateMixin, unittest.TestCase):
    Detector = WindowsRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class _LinuxExtensionsGateMixin(_ExtensionsGateMixin):
    def _make_globalstorage(self, ide_key: str) -> Path:
        gs = (self.home / ".config" / ide_key
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def test_host_install_not_required(self):
        """Regression for the dropped host-IDE AND-gate: a registry entry with NO
        host editor installed anywhere still detects on Linux."""
        self._make_registry_entry("Code")
        with patch("shutil.which", return_value=None):
            results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))


class TestLinuxClineIdeGate(_LinuxExtensionsGateMixin, unittest.TestCase):
    Detector = LinuxClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


class TestLinuxRooIdeGate(_LinuxExtensionsGateMixin, unittest.TestCase):
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
        """The editor launcher on PATH (``shutil.which`` resolves) -> installed
        when NOT admin, even with no install dir present."""
        with patch.object(win_helpers, "is_running_as_admin", return_value=False), \
             patch("shutil.which", side_effect=lambda n: r"C:\X\cursor.exe" if n == "cursor.exe" else None):
            installed, path = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, r"C:\X\cursor.exe")

    def test_path_launcher_skipped_when_admin(self):
        """Under an elevated admin scan the ``shutil.which`` PATH step is SKIPPED
        — it resolves the SCANNER's PATH, not user_home's, so honouring it would
        attribute the admin's editor to every user with extension residue (the
        cross-user FP). With no install dir present the result is (False, None)
        even though ``which`` would resolve. Mirrors the Linux/Gemini guard."""
        with patch.object(win_helpers, "is_running_as_admin", return_value=True), \
             patch("shutil.which", side_effect=lambda n: r"C:\X\cursor.exe" if n == "cursor.exe" else None):
            installed, path = win_helpers.is_windows_ide_installed("Cursor", self.home)
        self.assertFalse(installed)
        self.assertIsNone(path)


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
        """The editor binary on PATH (``shutil.which`` resolves) -> installed
        when NOT root."""
        with self._abs_redirect(), \
             patch.object(linux_helpers, "is_running_as_root", return_value=False), \
             patch("shutil.which", side_effect=lambda n: "/usr/bin/code" if n == "code" else None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertTrue(installed)
        self.assertEqual(path, "/usr/bin/code")

    def test_path_binary_skipped_when_root(self):
        """Under a root/MDM scan the ``shutil.which`` PATH step is SKIPPED — it
        resolves the SCANNER's PATH, not user_home's, so honouring it would
        attribute the scanner's editor to every user with extension residue (the
        cross-user FP). With no install dir present the result is (False, None)
        even though ``which`` would resolve. Mirrors the Gemini/Claude guard."""
        with self._abs_redirect(), \
             patch.object(linux_helpers, "is_running_as_root", return_value=True), \
             patch("shutil.which", side_effect=lambda n: "/usr/bin/code" if n == "code" else None):
            installed, path = linux_helpers.is_linux_ide_installed("Code", self.home)
        self.assertFalse(installed)
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
