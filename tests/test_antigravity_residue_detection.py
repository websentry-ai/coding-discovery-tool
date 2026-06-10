"""Residue-vs-real detection tests for Antigravity (macOS, Windows, Linux).

The fix stops treating ``~/.antigravity`` as proof of installation — it is a
residue config/data dir (a VS Code fork's user dir) that survives uninstall.
Detection now gates on a real install artifact removed on uninstall:

* macOS:   the ``.app`` bundle (``POSSIBLE_APP_PATHS``).
* Windows: ``Programs``/``Program Files`` dir holding ``Antigravity.exe`` or a
           ``resources`` tree.
* Linux:   a candidate install dir holding ``resources/app/product.json`` (or
           ``package.json``).

Both directions per OS: residue ``~/.antigravity`` only -> None; real artifact
-> detected.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod


class TestMacOSAntigravityResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.macos.antigravity import antigravity as mod
        self.mod = mod
        self.detector = mod.MacOSAntigravityDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.app = self.root / "Applications" / "Antigravity.app"

    def tearDown(self):
        self.tmp.cleanup()

    def test_residue_dir_only_not_detected(self):
        """``~/.antigravity`` present but no .app -> None (the FP fix)."""
        (self.home / ".antigravity").mkdir()
        # POSSIBLE_APP_PATHS points only at a non-existent tmp .app.
        with patch.object(self.detector, "POSSIBLE_APP_PATHS", [self.app]), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_app_bundle_detected(self):
        """A real ``Antigravity.app`` bundle -> detected (+version)."""
        plist = self.app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")
        with patch.object(self.detector, "POSSIBLE_APP_PATHS", [self.app]), \
             patch.object(self.mod, "run_command", return_value="1.5.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Antigravity")
        self.assertEqual(result["install_path"], str(self.app))
        self.assertEqual(result["version"], "1.5.0")


class TestWindowsAntigravityResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.windows.antigravity import antigravity as mod
        self.mod = mod
        self.detector = mod.WindowsAntigravityDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.install = self.root / "Programs" / "Antigravity"

    def tearDown(self):
        self.tmp.cleanup()

    def test_residue_dir_only_not_detected(self):
        """``~/.antigravity`` present but no install dir -> None."""
        (self.home / ".antigravity").mkdir()
        # _get_search_paths returns only a non-existent tmp install dir.
        with patch.object(self.detector, "_get_search_paths", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_exe_install_detected(self):
        """A Programs dir containing ``Antigravity.exe`` -> detected."""
        self.install.mkdir(parents=True, exist_ok=True)
        (self.install / "Antigravity.exe").write_text("")
        with patch.object(self.detector, "_get_search_paths", return_value=[self.install]), \
             patch.object(self.detector, "get_version", return_value="2.0.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "2.0.0")

    def test_resources_tree_install_detected(self):
        """A Programs dir with a ``resources`` tree (no exe) -> detected."""
        (self.install / "resources" / "app").mkdir(parents=True, exist_ok=True)
        (self.install / "resources" / "app" / "package.json").write_text(
            json.dumps({"version": "2.1.0"}))
        with patch.object(self.detector, "_get_search_paths", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "2.1.0")

    def test_admin_multiuser_other_user_install_detected(self):
        """MULTI-USER (WARNING #2): under a SYSTEM/admin scan, a per-user
        install ``C:\\Users\\<other>\\AppData\\Local\\Programs\\Antigravity``
        (with ``Antigravity.exe``) belonging to ANOTHER user is reached. Runs
        the REAL ``_get_search_paths`` (admin branch); only
        ``is_running_as_admin`` and the ``C:\\Users`` enumeration helper are
        patched, so the real artifact gate validates the exe.

        Fails against the pre-fix code which only built candidates from the
        scanner's own ``Path.home()``."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        # Use the FIRST name the detector tries (_PROGRAM_DIR_NAMES[0]) so the
        # returned install_path is deterministic on a case-insensitive FS,
        # where ``antigravity`` and ``Antigravity`` are the same physical dir.
        install = other_programs / self.detector._PROGRAM_DIR_NAMES[0]
        install.mkdir(parents=True, exist_ok=True)
        (install / "Antigravity.exe").write_text("")

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[other_programs]), \
             patch.object(self.detector, "get_version", return_value="3.0.0"), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))

    def test_admin_multiuser_other_user_residue_only_not_detected(self):
        """Under admin, another user's residue-only ``Programs\\Antigravity``
        dir (NO exe / no ``resources`` tree) still -> None: artifact gate
        preserved, no residue gate introduced."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        residue = other_programs / "Antigravity"
        residue.mkdir(parents=True, exist_ok=True)  # empty

        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.detector, "_other_user_program_dirs",
                          return_value=[other_programs]), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_non_admin_does_not_enumerate_other_users(self):
        """When NOT admin, the other-user enumeration must not run: a
        per-user install belonging to another user is NOT reported."""
        other_programs = self.root / "Users" / "other" / "AppData" / "Local" / "Programs"
        install = other_programs / "Antigravity"
        install.mkdir(parents=True, exist_ok=True)
        (install / "Antigravity.exe").write_text("")

        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            result = self.detector.detect()
        self.assertIsNone(result)

    # --- is_installed_for_user: user-scoped install probe ----------------
    # Used by the Windows Cline / Roo detectors to gate their Antigravity rows
    # on the install being present FOR THE USER being scanned (not for any
    # user reachable via the all-users admin enumeration). All cases rely on
    # ``C:\Program Files\Antigravity`` being absent on the test host (true on
    # macOS, and CI Windows runners have no Antigravity), so only the tmp
    # user_home decides the outcome.

    def test_is_installed_for_user_own_program_dir(self):
        """The user's OWN ``AppData/Local/Programs/Antigravity`` holding
        ``Antigravity.exe`` -> True."""
        user_install = (self.home / "AppData" / "Local" / "Programs"
                        / self.detector._PROGRAM_DIR_NAMES[0])
        user_install.mkdir(parents=True, exist_ok=True)
        (user_install / "Antigravity.exe").write_text("")
        self.assertTrue(self.detector.is_installed_for_user(self.home))

    def test_is_installed_for_user_resources_tree(self):
        """A user-scoped install dir with a ``resources`` tree (no exe) -> True."""
        user_install = (self.home / "AppData" / "Local" / "Programs"
                        / self.detector._PROGRAM_DIR_NAMES[0])
        (user_install / "resources").mkdir(parents=True, exist_ok=True)
        self.assertTrue(self.detector.is_installed_for_user(self.home))

    def test_is_installed_for_user_residue_only_false(self):
        """An empty user ``Programs/Antigravity`` dir (no exe / no resources) ->
        False — residue, not a real install (no residue gate introduced)."""
        user_install = (self.home / "AppData" / "Local" / "Programs"
                        / self.detector._PROGRAM_DIR_NAMES[0])
        user_install.mkdir(parents=True, exist_ok=True)  # empty
        self.assertFalse(self.detector.is_installed_for_user(self.home))

    def test_is_installed_for_user_absent_false(self):
        """No Antigravity artifact anywhere under the user's home -> False."""
        self.assertFalse(self.detector.is_installed_for_user(self.home))

    def test_is_installed_for_user_ignores_other_users_install(self):
        """THE CROSS-USER FIX: user B's check must NOT see user A's per-user
        Antigravity. ``is_installed_for_user`` consults only ``user_B``'s own
        home + machine-wide Program Files — never another user's Programs dir —
        so user A having Antigravity does not make it "installed for" user B.

        Fails against ``_find_app_path``, which under admin enumerates ALL
        users' Programs dirs and would report B as installed."""
        user_a_install = (self.root / "user_a" / "AppData" / "Local" / "Programs"
                          / self.detector._PROGRAM_DIR_NAMES[0])
        user_a_install.mkdir(parents=True, exist_ok=True)
        (user_a_install / "Antigravity.exe").write_text("")
        # user B (self.home) has NO Antigravity of its own.
        self.assertFalse(self.detector.is_installed_for_user(self.home))


class TestLinuxAntigravityResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.linux.antigravity import antigravity as mod
        self.mod = mod
        self.detector = mod.LinuxAntigravityDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.install = self.root / "opt" / "Antigravity"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_resource(self, filename: str, version: str) -> Path:
        target = self.install / "resources" / "app" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"name": "antigravity", "version": version}))
        return target

    def test_residue_dir_only_not_detected(self):
        """``~/.antigravity`` present, but the candidate install dirs hold no
        resource tree -> None. (``~/.antigravity`` is not a candidate dir.)"""
        (self.home / ".antigravity").mkdir()
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_product_json_install_detected(self):
        """An install dir with ``resources/app/product.json`` -> detected."""
        self._write_resource("product.json", "1.4.2")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "1.4.2")

    def test_package_json_install_detected(self):
        """Falls back to ``resources/app/package.json`` when no product.json."""
        self._write_resource("package.json", "0.9.0")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "0.9.0")

    def test_install_detected_when_version_key_missing(self):
        """WARNING #3: a real install whose ``resources/app/package.json``
        EXISTS but has NO ``version`` key is still detected (version reported
        as ``"Unknown"``). Detection is gated on the resource file existing,
        not on a parseable version.

        Fails against the pre-fix code, which only returned a hit when
        ``_read_version_file`` yielded a truthy version."""
        target = self.install / "resources" / "app" / "package.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Valid JSON, no "version" key.
        target.write_text(json.dumps({"name": "antigravity"}))
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[self.install]):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.install))
        self.assertEqual(result["version"], "Unknown")

    # --- FIX #3: /opt/antigravity-ide + arch-nested launcher gate --------

    def test_antigravity_ide_flat_launcher_detected(self):
        """FIX #3: ``/opt/antigravity-ide/Antigravity-IDE`` holding the flat
        ``antigravity-ide`` launcher (no flat ``resources/app`` tree) ->
        detected. Modelled on the Antigravity 2.0 tarball layout.

        Fails against the pre-fix gate, which only accepted a flat
        ``resources/app/product.json`` / ``package.json``."""
        ide_dir = self.root / "opt" / "antigravity-ide" / "Antigravity-IDE"
        launcher = ide_dir / "antigravity-ide"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[ide_dir]), \
             patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(ide_dir))
        self.assertEqual(result["version"], "Unknown")

    def test_arch_nested_launcher_detected(self):
        """FIX #3: an ARCH-NESTED ``<dir>/Antigravity-x64/antigravity`` launcher
        (the 2.0 tarball is arch-nested) -> detected even though the flat
        ``resources`` gate would miss it."""
        ide_dir = self.root / "opt" / "antigravity"
        launcher = ide_dir / "Antigravity-x64" / "antigravity"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[ide_dir]), \
             patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(ide_dir))

    def test_antigravity_ide_dirs_in_real_candidate_set(self):
        """GUARD: the new ``/opt/antigravity-ide`` dirs are in the REAL
        candidate set (not just injected in tests)."""
        candidates = self.detector._candidate_install_dirs()
        self.assertIn(Path("/opt/antigravity-ide"), candidates)
        self.assertIn(Path("/opt/antigravity-ide/Antigravity-IDE"), candidates)

    def test_residue_dir_only_still_not_detected_with_new_gate(self):
        """The new launcher gate must NOT re-open the residue FP: a bare
        ``~/.antigravity`` config dir (no resources tree, no launcher) under a
        candidate dir still -> None."""
        residue = self.home / ".antigravity"
        residue.mkdir(parents=True, exist_ok=True)
        (residue / "settings.json").write_text("{}")
        with patch.object(self.detector, "_candidate_install_dirs", return_value=[residue]):
            result = self.detector.detect()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
