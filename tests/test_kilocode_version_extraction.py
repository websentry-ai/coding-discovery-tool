"""Integration tests for KiloCode detection + version extraction.

KiloCode gates on (and reads its version from) the editor's ``extensions.json``
registry, not the extension's ``globalStorage/<ext-id>`` dir (which survives
uninstall). These tests drive the detector at ``_check_user_for_kilocode`` (the
per-user entry) and pin both directions: registry entry present -> detected with
that version; globalStorage residue with NO entry -> not detected (the FP kill).

The helper is pure ``pathlib`` + JSON, so there is no platform skip — every CI box
exercises the gate (otherwise the Linux runner would skip the very behaviour
under test).
"""

import json
import tempfile
import unittest
from pathlib import Path

KILO_EXT_ID = "kilocode.Kilo-Code"

# Per-editor extensions-registry dir (the file the detector now gates on).
_EXT_DIR = {"Code": ".vscode/extensions", "Cursor": ".cursor/extensions"}
# Per-OS globalStorage base (residue that must NOT, on its own, detect).
_GS_BASE = {
    "macos": lambda home, ide: home / "Library" / "Application Support" / ide,
    "windows": lambda home, ide: home / "AppData" / "Roaming" / ide,
}


def _write_registry(user_home: Path, ide_key: str, ext_id: str = KILO_EXT_ID,
                    version: str = "3.7.0") -> Path:
    """Write a KiloCode entry into ``<editor>/extensions/extensions.json`` and
    return the extensions dir (the detector's install_path)."""
    ext_dir = user_home / _EXT_DIR[ide_key]
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "extensions.json").write_text(json.dumps([
        {"identifier": {"id": ext_id}, "version": version,
         "relativeLocation": f"{ext_id}-{version}"}
    ]), encoding="utf-8")
    return ext_dir


def _make_globalstorage(user_home: Path, ide_key: str, os_kind: str) -> Path:
    """Create the extension's globalStorage residue dir (survives uninstall)."""
    gs = _GS_BASE[os_kind](user_home, ide_key) / "User" / "globalStorage" / KILO_EXT_ID
    gs.mkdir(parents=True, exist_ok=True)
    return gs


class _KiloDetectionMixin:
    """Shared assertions for macOS/Windows KiloCode detection via the registry
    gate. Subclasses set ``Detector`` and ``os_kind``."""

    Detector = None
    os_kind = None

    def setUp(self):
        self.detector = self.Detector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # --- registry entry present -> detected ------------------------------

    def test_registry_entry_detected_with_version(self):
        ext_dir = _write_registry(self.user_home, "Code", version="3.7.0")
        result = self.detector._check_user_for_kilocode(self.user_home)
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "3.7.0")
        self.assertEqual(result["install_path"], str(ext_dir))

    def test_cursor_registry_entry_detected(self):
        ext_dir = _write_registry(self.user_home, "Cursor", version="4.2.0")
        result = self.detector._check_user_for_kilocode(self.user_home)
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "4.2.0")
        self.assertEqual(result["install_path"], str(ext_dir))

    def test_lowercase_id_in_registry_detected(self):
        """The registry stores the lowercase id; the case-insensitive match still
        finds it against the display-cased ``KILOCODE_EXTENSION_ID`` constant."""
        _write_registry(self.user_home, "Code", ext_id="kilocode.kilo-code", version="5.1.0")
        result = self.detector._check_user_for_kilocode(self.user_home)
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "5.1.0")

    def test_first_supported_editor_wins(self):
        """Code is first in SUPPORTED_IDES; with both editors carrying an entry,
        Code's extensions dir is reported."""
        code_dir = _write_registry(self.user_home, "Code", version="1.0.0")
        _write_registry(self.user_home, "Cursor", version="2.0.0")
        result = self.detector._check_user_for_kilocode(self.user_home)
        self.assertEqual(result["install_path"], str(code_dir))
        self.assertEqual(result["version"], "1.0.0")

    # --- residue WITHOUT a registry entry -> NOT detected ----------------

    def test_globalstorage_residue_without_registry_entry_not_detected(self):
        """The FP kill: globalStorage residue (survives uninstall) present in BOTH
        editors but NO extensions.json registry entry -> not detected."""
        _make_globalstorage(self.user_home, "Code", self.os_kind)
        _make_globalstorage(self.user_home, "Cursor", self.os_kind)
        self.assertIsNone(self.detector._check_user_for_kilocode(self.user_home))

    def test_nothing_present_not_detected(self):
        self.assertIsNone(self.detector._check_user_for_kilocode(self.user_home))

    def test_registry_entry_for_other_extension_not_detected(self):
        """An extensions.json that lists a DIFFERENT extension -> not detected."""
        ext_dir = self.user_home / _EXT_DIR["Code"]
        ext_dir.mkdir(parents=True)
        (ext_dir / "extensions.json").write_text(json.dumps([
            {"identifier": {"id": "some.other-ext"}, "version": "9.9.9"}
        ]), encoding="utf-8")
        self.assertIsNone(self.detector._check_user_for_kilocode(self.user_home))


class TestMacOSKiloCodeDetection(_KiloDetectionMixin, unittest.TestCase):
    os_kind = "macos"

    @property
    def Detector(self):
        from scripts.coding_discovery_tools.macos.kilocode.kilocode import MacOSKiloCodeDetector
        return MacOSKiloCodeDetector


class TestWindowsKiloCodeDetection(_KiloDetectionMixin, unittest.TestCase):
    os_kind = "windows"

    @property
    def Detector(self):
        from scripts.coding_discovery_tools.windows.kilocode.kilocode import WindowsKiloCodeDetector
        return WindowsKiloCodeDetector


if __name__ == "__main__":
    unittest.main()
