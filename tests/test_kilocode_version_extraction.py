"""Unit tests for KiloCode version extraction across IDEs."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_extension(extensions_dir: Path, package_version: str = None, folder_suffix: str = "1.2.3") -> Path:
    """Create a fake VS Code/Cursor extension folder with optional package.json."""
    ext_dir = extensions_dir / f"kilocode.Kilo-Code-{folder_suffix}"
    ext_dir.mkdir(parents=True, exist_ok=True)
    if package_version is not None:
        (ext_dir / "package.json").write_text(json.dumps({"name": "Kilo-Code", "version": package_version}))
    return ext_dir


@unittest.skipUnless(sys.platform == "darwin", "macOS-only detector")
class TestMacOSKiloCodeVersion(unittest.TestCase):
    """Tests for macOS KiloCode version scoping & package.json reads."""

    def setUp(self):
        from scripts.coding_discovery_tools.macos.kilocode.kilocode import MacOSKiloCodeDetector
        self.detector = MacOSKiloCodeDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_version_from_package_json(self):
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="3.7.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "3.7.0")

    def test_falls_back_to_folder_suffix_when_package_json_unreadable(self):
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="2.5.1")
        (ext_dir / "package.json").write_text("not valid json {{{")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "2.5.1")

    def test_folder_suffix_preserves_prerelease_metadata(self):
        """Pre-release suffixes (1.2.3-pre.5) must NOT be truncated by rsplit."""
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="1.2.3-pre.5")
        (ext_dir / "package.json").write_text("not valid json")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "1.2.3-pre.5")

    def test_folder_suffix_preserves_beta_metadata(self):
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="1.0.0-beta.1")
        (ext_dir / "package.json").write_text("not valid json")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "1.0.0-beta.1")

    def test_scoped_to_requested_ide_only(self):
        """A Cursor lookup must NOT return a leftover VS Code version."""
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="1.0.0")
        # No Cursor extension folder exists at all
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertIsNone(version)

    def test_uses_cursor_extensions_dir_when_cursor_requested(self):
        _make_extension(self.user_home / ".cursor" / "extensions", package_version="4.2.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertEqual(version, "4.2.0")

    def test_returns_none_when_no_extensions_dir(self):
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertIsNone(version)


@unittest.skipUnless(sys.platform == "win32", "Windows-only detector")
class TestWindowsKiloCodeVersion(unittest.TestCase):
    """Tests for Windows KiloCode version scoping & package.json reads."""

    def setUp(self):
        from scripts.coding_discovery_tools.windows.kilocode.kilocode import WindowsKiloCodeDetector
        self.detector = WindowsKiloCodeDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_version_from_package_json(self):
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="3.7.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "3.7.0")

    def test_scoped_to_requested_ide_only(self):
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="1.0.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertIsNone(version)


if __name__ == "__main__":
    unittest.main()
