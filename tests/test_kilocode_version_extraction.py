"""Unit tests for KiloCode version extraction across IDEs."""

import json
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


class TestMacOSKiloCodeVersion(unittest.TestCase):
    """Tests for macOS KiloCode version scoping & package.json reads.

    No platform skip: ``_get_extension_version_for_user`` is pure ``pathlib``
    + JSON parsing, so the IDE-scoping regression guard runs on every CI box
    (otherwise the Linux runner would skip the very test we care about).
    """

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


class TestMacOSKiloCodeCheckUserInstallGating(unittest.TestCase):
    """
    Regression tests for the IDE install-gating in ``_check_user_for_kilocode``.

    The earlier implementation had a fallback that, when the first IDE with
    globalStorage didn't have a matching ``.app`` in /Applications, accepted
    *any other* installed IDE — but never updated the ``ide_with_extension``
    variable. The downstream version lookup then read from the wrong IDE's
    extensions directory.
    """

    def setUp(self):
        from scripts.coding_discovery_tools.macos.kilocode.kilocode import MacOSKiloCodeDetector
        self.detector = MacOSKiloCodeDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_globalstorage(self, ide_name: str) -> Path:
        """Create the globalStorage dir for the kilocode extension under one IDE."""
        gs = (
            self.user_home / "Library" / "Application Support"
            / ide_name / "User" / "globalStorage" / "kilocode.Kilo-Code"
        )
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def test_rejects_globalstorage_when_matching_ide_not_installed(self):
        """
        Trap config: Code has globalStorage but Code.app is NOT in /Applications;
        Cursor.app IS installed but has no kilocode globalStorage. The old
        fallback would have returned Code's globalStorage path glued to a
        Cursor-derived install signal and a "Unknown" version — the new
        behaviour returns None because no single IDE has both pieces.
        """
        self._make_globalstorage("Code")
        # No Cursor globalStorage exists

        def fake_check_ide(ide_name):
            # Code.app missing, Cursor.app installed
            return (ide_name == "Cursor", f"/Applications/{ide_name}.app")

        with patch.object(self.detector, "_check_ide_installation", side_effect=fake_check_ide):
            result = self.detector._check_user_for_kilocode(self.user_home)

        self.assertIsNone(
            result,
            "Detector must not pair globalStorage from an uninstalled IDE with a different installed IDE",
        )

    def test_prefers_ide_with_both_globalstorage_and_app(self):
        """
        When BOTH Code and Cursor have globalStorage but only Cursor.app is
        installed, the detector picks Cursor — and the version comes from
        Cursor's extensions dir, NOT VS Code's leftover one.
        """
        self._make_globalstorage("Code")
        cursor_gs = self._make_globalstorage("Cursor")

        # Stale leftover extension folder in Code's dir with the wrong version
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="9.9.9-stale")
        # Real KiloCode install in Cursor's extensions dir
        _make_extension(self.user_home / ".cursor" / "extensions", package_version="3.18.0")

        def fake_check_ide(ide_name):
            return (ide_name == "Cursor", f"/Applications/{ide_name}.app")

        with patch.object(self.detector, "_check_ide_installation", side_effect=fake_check_ide):
            result = self.detector._check_user_for_kilocode(self.user_home)

        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(cursor_gs))
        self.assertEqual(
            result["version"],
            "3.18.0",
            "Version must come from Cursor's extensions dir, not Code's stale leftover",
        )

    def test_returns_result_when_first_ide_has_both(self):
        """Happy path — first IDE in SUPPORTED_IDES has both globalStorage and .app."""
        code_gs = self._make_globalstorage("Code")
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="3.7.0")

        with patch.object(self.detector, "_check_ide_installation", return_value=(True, "/Applications/Code.app")):
            result = self.detector._check_user_for_kilocode(self.user_home)

        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(code_gs))
        self.assertEqual(result["version"], "3.7.0")


class TestWindowsKiloCodeVersion(unittest.TestCase):
    """Tests for Windows KiloCode version scoping & package.json reads.

    No platform skip — same reasoning as the macOS class: the helper is
    pure ``pathlib`` so the IDE-scoping regression guard runs on Linux CI.
    """

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

    def test_falls_back_to_folder_suffix_when_package_json_unreadable(self):
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="2.5.1")
        (ext_dir / "package.json").write_text("not valid json {{{")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "2.5.1")

    def test_folder_suffix_preserves_prerelease_metadata(self):
        ext_dir = _make_extension(self.user_home / ".vscode" / "extensions", folder_suffix="1.2.3-pre.5")
        (ext_dir / "package.json").write_text("not valid json")
        version = self.detector._get_extension_version_for_user(self.user_home, "Code")
        self.assertEqual(version, "1.2.3-pre.5")

    def test_scoped_to_requested_ide_only(self):
        _make_extension(self.user_home / ".vscode" / "extensions", package_version="1.0.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertIsNone(version)

    def test_uses_cursor_extensions_dir_when_cursor_requested(self):
        _make_extension(self.user_home / ".cursor" / "extensions", package_version="4.2.0")
        version = self.detector._get_extension_version_for_user(self.user_home, "Cursor")
        self.assertEqual(version, "4.2.0")


if __name__ == "__main__":
    unittest.main()
