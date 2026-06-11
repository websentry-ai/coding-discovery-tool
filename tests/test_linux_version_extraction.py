"""Unit tests for Linux version extraction across KiloCode, Antigravity, Replit."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


KILO_EXT_ID = "kilocode.Kilo-Code"


def _write_kilo_registry(user_home: Path, ide_key: str, ext_id: str = KILO_EXT_ID,
                         version: str = "3.7.0") -> Path:
    """Write a Kilo Code entry into ``<editor>/extensions/extensions.json`` (the
    install registry the detector now gates on)."""
    rel = {"Code": ".vscode/extensions", "Cursor": ".cursor/extensions"}[ide_key]
    ext_dir = user_home / rel
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "extensions.json").write_text(json.dumps([
        {"identifier": {"id": ext_id}, "version": version,
         "relativeLocation": f"{ext_id}-{version}"}
    ]), encoding="utf-8")
    return ext_dir


def _write_resource_json(install_dir: Path, filename: str, version: str) -> Path:
    target = install_dir / "resources" / "app" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"name": install_dir.name, "version": version}))
    return target


class TestLinuxKiloCodeVersion(unittest.TestCase):
    """Kilo Code on Linux now gates on (and reads its version from) the editor's
    ``extensions.json`` registry, via ``detect()`` -> ``_check_user_for_kilocode``.

    ``get_linux_user_homes`` is pinned to the hermetic tmp home so the scan is
    isolated from the CI box's real ``/home``."""

    def setUp(self):
        from scripts.coding_discovery_tools.linux.kilocode.kilocode import LinuxKiloCodeDetector
        self.detector = LinuxKiloCodeDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)
        self._homes = patch(
            "scripts.coding_discovery_tools.linux.kilocode.kilocode.get_linux_user_homes",
            return_value=[self.user_home],
        )
        self._homes.start()

    def tearDown(self):
        self._homes.stop()
        self.tmp.cleanup()

    def test_reads_version_from_registry_entry(self):
        _write_kilo_registry(self.user_home, "Code", version="3.7.0")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "3.7.0")
        self.assertEqual(result["install_path"], str(self.user_home / ".vscode" / "extensions"))

    def test_uses_cursor_registry_when_cursor_has_entry(self):
        _write_kilo_registry(self.user_home, "Cursor", version="4.2.0")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "4.2.0")
        self.assertEqual(result["install_path"], str(self.user_home / ".cursor" / "extensions"))

    def test_lowercase_id_in_registry_detected(self):
        """The registry stores the lowercase id; the case-insensitive match still
        finds it against the display-cased constant."""
        _write_kilo_registry(self.user_home, "Code", ext_id="kilocode.kilo-code", version="5.0.0")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "5.0.0")

    def test_globalstorage_residue_without_registry_entry_not_detected(self):
        """The FP kill: globalStorage residue (which survives uninstall) present but
        NO extensions.json registry entry -> not detected."""
        gs = self.user_home / ".config" / "Code" / "User" / "globalStorage" / KILO_EXT_ID
        gs.mkdir(parents=True)
        self.assertIsNone(self.detector.detect())


class TestLinuxAntigravityVersion(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.linux.antigravity import antigravity as mod
        self.mod = mod
        self.detector = mod.LinuxAntigravityDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_install = Path(self.tmp.name) / "Antigravity"
        self.fake_install.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_version_from_product_json(self):
        _write_resource_json(self.fake_install, "product.json", "1.4.2")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.fake_install]):
            version = self.detector.get_version()
        self.assertEqual(version, "1.4.2")

    def test_falls_back_to_package_json_when_product_missing(self):
        _write_resource_json(self.fake_install, "package.json", "0.9.0")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.fake_install]):
            version = self.detector.get_version()
        self.assertEqual(version, "0.9.0")

    def test_returns_none_when_no_install_found(self):
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[]):
            version = self.detector.get_version()
        self.assertIsNone(version)


class TestLinuxReplitVersion(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.linux.replit import replit as mod
        self.mod = mod
        self.detector = mod.LinuxReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_install = Path(self.tmp.name) / "Replit"
        self.fake_install.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_version_from_package_json(self):
        _write_resource_json(self.fake_install, "package.json", "2.0.1")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.fake_install]):
            with patch.object(self.detector, "_version_via_command", return_value=None):
                version = self.detector.get_version()
        self.assertEqual(version, "2.0.1")

    def test_falls_back_to_command_when_no_install(self):
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[]):
            with patch.object(self.detector, "_version_via_command", return_value="0.5.0"):
                version = self.detector.get_version()
        self.assertEqual(version, "0.5.0")

    def test_returns_none_when_no_install_and_no_command(self):
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[]):
            with patch.object(self.detector, "_version_via_command", return_value=None):
                version = self.detector.get_version()
        self.assertIsNone(version)


if __name__ == "__main__":
    unittest.main()
