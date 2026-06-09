"""Residue-vs-real detection tests for Replit (macOS, Windows, Linux).

The fix stops treating Replit's user *data* directory as proof of installation
(``~/Library/Application Support/Replit``, ``%APPDATA%\\Roaming\\Replit``,
``~/.config/Replit``). Those survive uninstall and produced false positives.
Detection now gates on a real install artifact removed on uninstall:

* macOS:   ``/Applications/Replit.app``.
* Windows: a ``Programs``/``Program Files`` dir holding ``Replit.exe`` or a
           ``resources\\app\\package.json`` resource tree.
* Linux:   a candidate install dir holding ``resources/app/package.json`` (with
           a ``which replit`` backstop).

Both directions per OS: data-dir-only -> None; real artifact -> detected.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestMacOSReplitResidue(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.macos.replit import replit as mod
        self.mod = mod
        self.detector = mod.MacOSReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = self.root / "Applications" / "Replit.app"

    def tearDown(self):
        self.tmp.cleanup()

    def test_data_dir_only_not_detected(self):
        """A user data dir but no ``Replit.app`` -> None. The data dir is no
        longer read at all, so we simply assert the app gate is the only path:
        ``APPLICATION_PATH`` points at a non-existent tmp .app."""
        with patch.object(self.detector, "APPLICATION_PATH", self.app):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_app_bundle_detected(self):
        """A real ``Replit.app`` bundle -> detected (+version)."""
        plist = self.app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")
        with patch.object(self.detector, "APPLICATION_PATH", self.app), \
             patch.object(self.mod, "run_command", return_value="1.8.0\n"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Replit")
        self.assertEqual(result["install_path"], str(self.app))
        self.assertEqual(result["version"], "1.8.0")


class TestWindowsReplitResidue(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.windows.replit import replit as mod
        self.mod = mod
        self.detector = mod.WindowsReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = self.root / "Programs" / "Replit"

    def tearDown(self):
        self.tmp.cleanup()

    def test_data_dir_only_not_detected(self):
        """``%APPDATA%\\Roaming\\Replit`` data dir but no real install dir ->
        None. Candidate install paths point only at a non-existent tmp dir."""
        roaming = self.root / "AppData" / "Roaming" / "Replit"
        roaming.mkdir(parents=True, exist_ok=True)
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_exe_install_detected(self):
        """A Programs dir containing ``Replit.exe`` -> detected."""
        self.install.mkdir(parents=True, exist_ok=True)
        (self.install / "Replit.exe").write_text("")
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.install]), \
             patch.object(self.detector, "get_version", return_value="1.8.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "1.8.0")

    def test_resources_tree_install_detected(self):
        """A Programs dir with ``resources\\app\\package.json`` (no exe) ->
        detected."""
        pkg = self.install / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"version": "2.1.4"}))
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "2.1.4")

    def test_admin_multiuser_other_user_install_detected(self):
        """MULTI-USER (WARNING #2): under a SYSTEM/admin scan, a per-user
        squirrel install ``C:\\Users\\<other>\\AppData\\Local\\Programs\\
        Replit\\Replit.exe`` belonging to ANOTHER user is reached. This runs
        the REAL ``_candidate_install_paths`` (admin branch) — only
        ``is_running_as_admin`` and the ``C:\\Users`` enumeration helper are
        patched, so the real artifact gate validates the exe.

        Fails against the pre-fix rewrite which never walked other users'
        dirs under admin."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        # Use the FIRST candidate name (INSTALL_DIR_NAMES[0]) so the returned
        # install_path is deterministic on a case-insensitive FS.
        install = other_programs / self.detector.INSTALL_DIR_NAMES[0]
        install.mkdir(parents=True, exist_ok=True)
        (install / "Replit.exe").write_text("")

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[other_programs]), \
             patch.object(self.detector, "get_version", return_value="3.0.0"), \
             patch.dict(self.mod.os.environ, {}, clear=True):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))

    def test_admin_multiuser_other_user_residue_only_not_detected(self):
        """Under admin, another user's residue-only dir (a bare
        ``Programs\\Replit`` with NO exe / resource tree) still -> None: the
        artifact gate is preserved, no residue gate is introduced."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        residue = other_programs / "Replit"
        residue.mkdir(parents=True, exist_ok=True)  # empty: no exe, no resources

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[other_programs]), \
             patch.dict(self.mod.os.environ, {}, clear=True):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_non_admin_does_not_enumerate_other_users(self):
        """When NOT admin, the other-user enumeration must not run: a
        per-user install belonging to another user is NOT reported."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        install = other_programs / "Replit"
        install.mkdir(parents=True, exist_ok=True)
        (install / "Replit.exe").write_text("")

        # Strip env so the scanner's own %LOCALAPPDATA% can't match either.
        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod.Path, "home", return_value=self.root / "scanner"), \
             patch.dict(self.mod.os.environ, {}, clear=True):
            result = self.detector.detect()
        self.assertIsNone(result)


class TestLinuxReplitResidue(unittest.TestCase):
    def setUp(self):
        from scripts.coding_discovery_tools.linux.replit import replit as mod
        self.mod = mod
        self.detector = mod.LinuxReplitDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.install = self.root / "opt" / "Replit"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_package_json(self, version: str) -> Path:
        pkg = self.install / "resources" / "app" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": "replit", "version": version}))
        return pkg

    def test_config_dir_only_not_detected(self):
        """``~/.config/Replit`` data dir, candidate install dirs hold no
        resource tree, and ``which replit`` is empty -> None (FP fix). The
        ``run_command`` stub neutralises the ``which`` backstop."""
        cfg = self.home / ".config" / "Replit"
        cfg.mkdir(parents=True, exist_ok=True)
        # Candidate dir is the bare config dir — exists, but no resource tree.
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[cfg]), \
             patch.object(self.mod, "run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_resource_tree_install_detected(self):
        """An install dir with ``resources/app/package.json`` -> detected."""
        self._write_package_json("2.0.1")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]), \
             patch.object(self.detector, "_version_via_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "2.0.1")

    def test_which_replit_backstop_detected(self):
        """No resource-tree install, but ``which replit`` resolves to a real
        path -> detected. Proves the PATH backstop survives."""
        replit_bin = self.root / "usr" / "bin" / "replit"
        replit_bin.parent.mkdir(parents=True, exist_ok=True)
        replit_bin.write_text("")

        def fake_run(cmd, *a, **k):
            # ``which replit`` -> the real tmp path; version probes -> None.
            if cmd[:1] == ["which"]:
                return str(replit_bin)
            return None

        with patch.object(self.detector, "_candidate_install_dirs", return_value=[]), \
             patch.object(self.mod, "run_command", side_effect=fake_run):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(replit_bin))


if __name__ == "__main__":
    unittest.main()
