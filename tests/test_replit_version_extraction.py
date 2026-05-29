"""Unit tests for Replit Desktop version extraction (macOS + Windows).

Trimmed to regression-guard + primary-path coverage: each test either
locks in a fix flagged by review or exercises the main success path.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestMacOSReplitVersion(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.macos.replit import replit as mod
        self.mod = mod
        self.detector = mod.MacOSReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_app = Path(self.tmp.name) / "Replit.app"
        self.fake_app.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_plist_first_when_passed_explicit_app_path(self):
        """
        Regression guard (Greptile): get_version() must honour the explicit
        ``app_path`` argument instead of reading ``self.APPLICATION_PATH``.
        Otherwise the redundancy-elision in detect() is defeated and every
        host reads /Applications/Replit.app regardless of caller.
        """
        plist = self.fake_app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")

        with patch.object(self.mod, "run_command", return_value="1.8.0\n") as rc:
            version = self.detector.get_version(self.fake_app)

        self.assertEqual(version, "1.8.0")
        args = rc.call_args.args[0]
        self.assertIn(str(plist), args)
        self.assertNotIn("/Applications/Replit.app", " ".join(args))

    def test_falls_back_to_package_json_when_plist_unreadable(self):
        plist = self.fake_app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")
        pkg = self.fake_app / "Contents" / "Resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": "replit", "version": "2.1.4"}))

        with patch.object(self.mod, "run_command", return_value=None):
            version = self.detector.get_version(self.fake_app)

        self.assertEqual(version, "2.1.4")


class TestWindowsReplitVersion(unittest.TestCase):
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

    def test_reads_version_from_package_json(self):
        self._write_package_json("1.8.0")
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.fake_install]):
            self.assertEqual(self.detector.get_version(), "1.8.0")

    def test_falls_back_to_powershell_when_package_json_unreadable(self):
        """
        Regression guard: the PS command must use ``-LiteralPath`` rather
        than the earlier ``repr(str(path))`` form (which produced doubled
        backslashes inside single-quoted PowerShell strings).
        """
        pkg = self.fake_install / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("not valid json {{{")
        (self.fake_install / "Replit.exe").write_text("")

        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.fake_install]):
            with patch.object(self.mod, "run_command", return_value="3.0.0-rc.1\n") as rc:
                version = self.detector.get_version()

        self.assertEqual(version, "3.0.0-rc.1")
        ps_command = rc.call_args.args[0][2]
        self.assertIn("-LiteralPath", ps_command)
        self.assertNotIn("\\\\", ps_command)

    def test_escapes_single_quotes_in_path(self):
        """
        Regression guard: a single quote inside the path must be doubled
        (``'`` -> ``''``) so the PowerShell single-quoted literal can't
        end prematurely and treat the remainder as code.
        """
        tricky_dir = Path(self.tmp.name) / "user's"
        tricky_dir.mkdir(parents=True, exist_ok=True)
        pkg = tricky_dir / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("not json")
        (tricky_dir / "Replit.exe").write_text("")

        with patch.object(self.detector, "_candidate_install_paths", return_value=[tricky_dir]):
            with patch.object(self.mod, "run_command", return_value="0.1.0\n") as rc:
                self.detector.get_version()

        ps_command = rc.call_args.args[0][2]
        self.assertIn("user''s", ps_command)

    def test_get_version_scans_supplied_user_home_first(self):
        """
        Regression guard for the SYSTEM/Admin multi-user blind spot.

        Under ``az run-command`` / Run-Command-Manager elevation, the
        process runs as SYSTEM, so ``%LOCALAPPDATA%`` resolves to
        ``C:\\Windows\\system32\\config\\systemprofile\\AppData\\Local`` —
        not the scanned user's AppData. detect() now plumbs the
        discovered user_home through to get_version(); _candidate_install_paths
        must put ``<user_home>\\AppData\\Local\\Programs`` ahead of the env-var
        path so the per-user squirrel install is actually found.
        """
        fake_user_home = Path(self.tmp.name) / "alice"
        install_dir = fake_user_home / "AppData" / "Local" / "Programs" / "Replit"
        install_dir.mkdir(parents=True, exist_ok=True)
        pkg = install_dir / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": "replit", "version": "3.1.0"}))

        # Point the env var at SOMETHING ELSE so the test would fail loudly
        # if get_version() fell back to the env-var path instead of using
        # the supplied user_home.
        wrong_localappdata = Path(self.tmp.name) / "system-profile" / "AppData" / "Local"
        wrong_localappdata.mkdir(parents=True, exist_ok=True)
        env = {"LOCALAPPDATA": str(wrong_localappdata),
               "ProgramFiles": str(Path(self.tmp.name) / "Program Files"),
               "ProgramFiles(x86)": str(Path(self.tmp.name) / "Program Files (x86)")}
        with patch.dict("os.environ", env, clear=False):
            version = self.detector.get_version(user_home=fake_user_home)

        self.assertEqual(version, "3.1.0")


if __name__ == "__main__":
    unittest.main()
