"""Unit tests for Linux version extraction across KiloCode, Antigravity, Replit."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_extension(extensions_dir: Path, package_version: str = None, folder_suffix: str = "1.2.3") -> Path:
    ext_dir = extensions_dir / f"kilocode.Kilo-Code-{folder_suffix}"
    ext_dir.mkdir(parents=True, exist_ok=True)
    if package_version is not None:
        (ext_dir / "package.json").write_text(json.dumps({"name": "Kilo-Code", "version": package_version}))
    return ext_dir


def _write_resource_json(install_dir: Path, filename: str, version: str) -> Path:
    target = install_dir / "resources" / "app" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"name": install_dir.name, "version": version}))
    return target


class TestLinuxKiloCodeVersion(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.linux.kilocode.kilocode import LinuxKiloCodeDetector
        self.detector = LinuxKiloCodeDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_version_from_package_json(self):
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="3.7.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "3.7.0")

    def test_scoped_to_requested_ide_only(self):
        """A Cursor lookup must NOT return a leftover VS Code version."""
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="1.0.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertIsNone(version)

    def test_uses_cursor_extensions_dir_when_cursor_requested(self):
        _make_extension(self.user_home / ".cursor" / "extensions", package_version="4.2.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertEqual(version, "4.2.0")

    def test_folder_suffix_fallback(self):
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="2.5.1")
        (ext_dir / "package.json").write_text("not valid json {{{")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "2.5.1")


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
