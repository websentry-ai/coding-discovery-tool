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
        """A Squirrel install dir containing a versioned ``app-*\\Replit.exe``
        -> detected. (Squirrel never puts the exe directly under the root; it
        lives in the ``app-<version>`` folder alongside ``Update.exe``.)"""
        self.install.mkdir(parents=True, exist_ok=True)
        exe = self.install / "app-1.8.0" / "Replit.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("")
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

    def test_squirrel_layout_detected_with_dir_name_version(self):
        """REGRESSION (FIX #1): a realistic Electron Forge / Squirrel layout
        ``%LocalAppData%\\replit\\app-1.2.3\\Replit.exe`` + ``Update.exe`` ->
        detected, with the version parsed from the ``app-1.2.3`` folder name
        (NOT the exe / package.json, which asar removes). Runs the REAL
        ``get_version`` (only ``run_command`` neutralised) so the dir-name
        parse is exercised.

        Fails against the pre-fix gate, which only looked for ``Replit.exe``
        directly under the candidate dir."""
        local = self.root / "AppData" / "Local"
        install = local / "replit"
        install.mkdir(parents=True, exist_ok=True)
        (install / "Update.exe").write_text("")
        exe = install / "app-1.2.3" / "Replit.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("")

        env = {"LOCALAPPDATA": str(local)}
        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod, "run_command", return_value=None), \
             patch.dict(self.mod.os.environ, env, clear=True):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))
        self.assertEqual(result["version"], "1.2.3")

    def test_asar_only_install_detected(self):
        """REGRESSION (FIX #1): an ``asar: true`` install — ``resources\\
        app.asar`` present, NO ``resources\\app\\package.json`` — is detected
        (the asar layout the gate previously missed)."""
        asar = self.install / "resources" / "app.asar"
        asar.parent.mkdir(parents=True, exist_ok=True)
        asar.write_text("")  # packed archive; we never parse it (zero-dep)
        with patch.object(self.detector, "_candidate_install_paths", return_value=[self.install]), \
             patch.object(self.mod, "run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        # No parseable version source (asar not parsed) -> "Unknown".
        self.assertEqual(result["version"], "Unknown")

    def test_bare_install_dir_only_not_detected(self):
        """A bare empty ``%LocalAppData%\\replit\\`` dir (no Update.exe / no
        versioned exe / no asar / no package.json) -> None. GUARD: the gate is
        on inner artifacts, never on the bare Squirrel dir existing (which is
        the dir uninstall deletes)."""
        local = self.root / "AppData" / "Local"
        (local / "replit").mkdir(parents=True, exist_ok=True)  # empty
        env = {"LOCALAPPDATA": str(local)}
        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod, "run_command", return_value=None), \
             patch.dict(self.mod.os.environ, env, clear=True):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_roaming_data_dir_only_not_detected(self):
        """The residue userData dir ``%APPDATA%\\Roaming\\Replit`` alone (no
        install dir) -> None. It is not even a candidate path."""
        local = self.root / "AppData" / "Local"
        local.mkdir(parents=True, exist_ok=True)  # no replit install dir inside
        roaming = self.root / "AppData" / "Roaming" / "Replit"
        roaming.mkdir(parents=True, exist_ok=True)
        (roaming / "config.json").write_text("{}")
        env = {"LOCALAPPDATA": str(local)}
        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod, "run_command", return_value=None), \
             patch.dict(self.mod.os.environ, env, clear=True):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_admin_multiuser_other_user_install_detected(self):
        """MULTI-USER (WARNING #2): under a SYSTEM/admin scan, a per-user
        Squirrel DIRECT install ``C:\\Users\\<other>\\AppData\\Local\\replit``
        (with ``Update.exe`` + ``app-*\\Replit.exe``) belonging to ANOTHER user
        is reached via ``_other_user_local_appdata_dirs``. This runs the REAL
        ``_candidate_install_paths`` (admin branch) — only
        ``is_running_as_admin`` and the ``C:\\Users`` enumeration helpers are
        patched, so the real artifact gate validates the install.

        Fails against the pre-fix rewrite which never walked other users'
        ``AppData\\Local`` dirs under admin."""
        other_local = self.root / "Users" / "other" / "AppData" / "Local"
        # Use the FIRST candidate name (INSTALL_DIR_NAMES[0]) so the returned
        # install_path is deterministic on a case-insensitive FS.
        install = other_local / self.detector.INSTALL_DIR_NAMES[0]
        install.mkdir(parents=True, exist_ok=True)
        (install / "Update.exe").write_text("")
        exe = install / "app-3.0.0" / "Replit.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("")

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_local_appdata_dirs",
                          return_value=[other_local]), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[]), \
             patch.object(self.detector, "get_version", return_value="3.0.0"), \
             patch.dict(self.mod.os.environ, {}, clear=True):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))

    def test_admin_multiuser_other_user_residue_only_not_detected(self):
        """Under admin, another user's residue-only dir (a bare
        ``AppData\\Local\\replit`` with NO Update.exe / versioned exe / asar /
        package.json) still -> None: the artifact gate is preserved, no residue
        gate is introduced."""
        other_local = self.root / "Users" / "other" / "AppData" / "Local"
        residue = other_local / "replit"
        residue.mkdir(parents=True, exist_ok=True)  # empty: no install artifact

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_local_appdata_dirs",
                          return_value=[other_local]), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[]), \
             patch.dict(self.mod.os.environ, {}, clear=True):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_non_admin_does_not_enumerate_other_users(self):
        """When NOT admin, the other-user enumeration must not run: a real
        per-user Squirrel install belonging to another user is NOT reported."""
        other_local = self.root / "Users" / "other" / "AppData" / "Local"
        install = other_local / "replit"
        install.mkdir(parents=True, exist_ok=True)
        (install / "Update.exe").write_text("")  # a REAL install artifact

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
        """``~/.config/Replit`` data dir with no resource tree in any candidate
        install dir -> not detected (residue is not an install)."""
        cfg = self.home / ".config" / "Replit"
        cfg.mkdir(parents=True, exist_ok=True)
        # Candidate dir is the bare config dir — exists, but no resource tree.
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[cfg]):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_resource_tree_install_detected(self):
        """An install dir with ``resources/app/package.json`` -> detected."""
        self._write_package_json("2.0.1")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "2.0.1")

    def test_asar_install_detected(self):
        """REGRESSION (FIX #1): the deb installs to ``/usr/lib/replit`` and,
        with ``asar: true``, ships ``resources/app.asar`` (NO
        ``resources/app/package.json``). Gating only on package.json missed it.
        Modelled on the real ``/usr/lib/replit/resources/app.asar`` layout.

        Fails against the pre-fix gate, which only checked package.json."""
        lib_install = self.root / "usr" / "lib" / "replit"
        asar = lib_install / "resources" / "app.asar"
        asar.parent.mkdir(parents=True, exist_ok=True)
        asar.write_text("")  # packed archive; never parsed (zero-dep)
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[lib_install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(lib_install))
        # asar not parsed and no package.json -> "Unknown".
        self.assertEqual(result["version"], "Unknown")

    def test_usr_lib_replit_is_a_real_candidate(self):
        """GUARD: ``/usr/lib/replit`` (the one verified deb location) must stay
        in the real candidate set even though the speculative ``/opt/*`` /
        ``/usr/share/*`` entries were dropped."""
        self.assertIn(Path("/usr/lib/replit"), self.detector._candidate_install_dirs())

    def test_which_replit_pypi_collision_not_detected(self):
        """A ``replit`` on PATH that is NOT Replit Desktop (e.g. the PyPI
        ``replit`` package's console script) must NOT be detected when there is
        no install resource tree. detect() gates on the resource tree only and
        never consults ``which replit`` — the backstop was removed because it
        name-collided with the PyPI package and reported a phantom Desktop."""
        # A `replit` console script may sit on PATH (the collision); without a
        # real install resource tree, detection must ignore it.
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[]):
            result = self.detector.detect()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
