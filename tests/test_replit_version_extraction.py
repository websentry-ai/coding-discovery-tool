"""Unit tests for Replit Desktop version extraction (macOS + Windows)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestMacOSReplitVersion(unittest.TestCase):
    """Tests for macOS Replit get_version() — Info.plist + package.json fallback.

    No platform skip: the JSON-read path is pure ``pathlib``, and the plist
    path is exercised via a patched ``run_command``. Both work on every CI
    runner.
    """

    def setUp(self):
        from scripts.coding_discovery_tools.macos.replit import replit as mod
        self.mod = mod
        self.detector = mod.MacOSReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_app = Path(self.tmp.name) / "Replit.app"
        self.fake_app.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_when_no_install(self):
        # Don't pass app_path and force _check_application_installation -> None
        with patch.object(self.detector, "_check_application_installation", return_value=None):
            self.assertIsNone(self.detector.get_version())

    def test_reads_plist_first_when_passed_explicit_app_path(self):
        plist = self.fake_app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")

        with patch.object(self.mod, "run_command", return_value="1.8.0\n") as rc:
            version = self.detector.get_version(self.fake_app)

        self.assertEqual(version, "1.8.0")
        rc.assert_called_once()
        # The plist path passed to `defaults read` must come from the
        # explicit ``app_path``, NOT from self.APPLICATION_PATH — otherwise
        # the redundancy-elision fix is defeated and we end up reading
        # /Applications/Replit.app on every box regardless of caller intent.
        args = rc.call_args.args[0]
        self.assertIn(str(plist), args)
        self.assertNotIn("/Applications/Replit.app", " ".join(args))

    def test_falls_back_to_package_json_when_plist_unreadable(self):
        # plist exists but defaults read returns empty (simulating a broken plist)
        plist = self.fake_app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")
        # Provide a valid package.json
        pkg = self.fake_app / "Contents" / "Resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": "replit", "version": "2.1.4"}))

        with patch.object(self.mod, "run_command", return_value=None):
            version = self.detector.get_version(self.fake_app)

        self.assertEqual(version, "2.1.4")

    def test_falls_through_to_none_when_both_sources_missing(self):
        # No plist file, no package.json
        with patch.object(self.mod, "run_command", return_value=None):
            version = self.detector.get_version(self.fake_app)
        self.assertIsNone(version)


class TestWindowsReplitVersion(unittest.TestCase):
    """Tests for Windows Replit get_version() — package.json + PowerShell fallback."""

    def setUp(self):
        from scripts.coding_discovery_tools.windows.replit import replit as mod
        self.mod = mod
        self.detector = mod.WindowsReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_install = Path(self.tmp.name) / "Replit"
        self.fake_install.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_package_json(self, version: str) -> Path:
        pkg = self.fake_install / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": "replit", "version": version}))
        return pkg

    def test_returns_none_when_no_install_found(self):
        with patch.object(self.detector, "_candidate_install_paths", return_value=[]):
            self.assertIsNone(self.detector.get_version())

    def test_reads_version_from_package_json(self):
        self._write_package_json("1.8.0")
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.fake_install]):
            self.assertEqual(self.detector.get_version(), "1.8.0")

    def test_skips_candidate_when_directory_missing(self):
        # Two candidates: one missing, one valid — must return the valid one
        missing = Path(self.tmp.name) / "Does-Not-Exist"
        self._write_package_json("0.9.1")
        with patch.object(self.detector, "_candidate_install_paths",
                          return_value=[missing, self.fake_install]):
            self.assertEqual(self.detector.get_version(), "0.9.1")

    def test_falls_back_to_powershell_when_package_json_unreadable(self):
        # package.json is broken; .exe present so the exe-fallback path runs
        pkg = self.fake_install / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("not valid json {{{")
        exe = self.fake_install / "Replit.exe"
        exe.write_text("")  # presence is what matters; we mock the powershell call

        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.fake_install]):
            with patch.object(self.mod, "run_command", return_value="3.0.0-rc.1\n") as rc:
                version = self.detector.get_version()

        self.assertEqual(version, "3.0.0-rc.1")
        rc.assert_called_once()
        # The PS command should use -LiteralPath with single-quoted path so
        # backslashes don't get doubled. Greptile flagged the prior repr()
        # form for emitting 'C:\\Users\\...' (literal double-backslash).
        ps_command = rc.call_args.args[0][2]
        self.assertIn("-LiteralPath", ps_command)
        self.assertNotIn("\\\\", ps_command)

    def test_escapes_single_quotes_in_path(self):
        # Path containing a single quote must be doubled, not corrupted.
        tricky_dir = Path(self.tmp.name) / "user's"
        tricky_dir.mkdir(parents=True, exist_ok=True)
        pkg = tricky_dir / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("not json")
        exe = tricky_dir / "Replit.exe"
        exe.write_text("")

        with patch.object(self.detector, "_candidate_install_paths", return_value=[tricky_dir]):
            with patch.object(self.mod, "run_command", return_value="0.1.0\n") as rc:
                self.detector.get_version()

        ps_command = rc.call_args.args[0][2]
        # A single quote in the original path must appear as two single
        # quotes inside the PS single-quoted literal — otherwise the
        # literal ends prematurely and PowerShell treats the remainder
        # as code.
        self.assertIn("user''s", ps_command)

    def test_candidate_install_paths_includes_user_and_system_dirs(self):
        # Just sanity-check that the candidate list is non-empty and
        # references at least one of the conventional Windows roots. We use
        # os.path.normpath() to compare so the test is portable to a macOS
        # CI box (where PosixPath joins with forward slashes).
        env = {"LOCALAPPDATA": "C:\\Users\\test\\AppData\\Local",
               "ProgramFiles": "C:\\Program Files",
               "ProgramFiles(x86)": "C:\\Program Files (x86)"}
        with patch.dict(os.environ, env, clear=False):
            candidates = self.detector._candidate_install_paths()

        normalized = [os.path.normpath(str(p)).replace("\\", "/") for p in candidates]
        self.assertTrue(
            any("AppData/Local/Programs" in p for p in normalized),
            f"No AppData/Local/Programs candidate found in {normalized}",
        )
        self.assertTrue(
            any("Program Files" in p for p in normalized),
            f"No Program Files candidate found in {normalized}",
        )


if __name__ == "__main__":
    unittest.main()
