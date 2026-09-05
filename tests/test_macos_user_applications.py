"""macOS: apps installed to the user's ``~/Applications`` must be detected.

A user without admin rights cannot write to ``/Applications``, so on a managed
fleet their app lands in ``~/Applications`` instead.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.macos_extraction_helpers import macos_app_candidates

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"


class TestMacosAppCandidates(unittest.TestCase):
    def test_machine_wide_path_gains_user_sibling(self):
        candidates = macos_app_candidates(Path("/Applications/Cursor.app"), Path("/Users/alice"))
        self.assertEqual(
            [Path("/Applications/Cursor.app"), Path("/Users/alice/Applications/Cursor.app")],
            candidates,
        )

    def test_non_machine_path_is_left_alone(self):
        """A tmp path a test patched in must not gain a sibling."""
        app = Path("/tmp/fixture/Applications/Cursor.app")
        self.assertEqual([app], macos_app_candidates(app, Path("/Users/alice")))

    def test_defaults_to_current_home_without_a_scoped_user(self):
        candidates = macos_app_candidates(Path("/Applications/Cursor.app"))
        self.assertEqual(Path.home() / "Applications" / "Cursor.app", candidates[1])


class _UserApplicationsCase(unittest.TestCase):
    """Isolated machine-wide dir plus one scanned user's home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.machine_apps = root / "Applications"
        self.machine_apps.mkdir(parents=True, exist_ok=True)
        self.user_home = root / "Users" / "alice"
        self.user_apps = self.user_home / "Applications"
        self.user_apps.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _bundle(self, parent: Path, name: str) -> Path:
        app = parent / name
        plist = app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text("<plist/>")
        return app


class TestCursorUserApplications(_UserApplicationsCase):
    def setUp(self):
        super().setUp()
        from scripts.coding_discovery_tools.macos.cursor import cursor as mod
        self.mod = mod
        self.detector = mod.MacOSCursorDetector()
        self.detector.user_home = self.user_home

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps), \
             patch.object(self.detector, "DEFAULT_APP_PATH", self.machine_apps / "Cursor.app"), \
             patch.object(self.mod, "run_command", return_value="1.7.0"):
            return self.detector.detect()

    def test_user_local_install_detected(self):
        app = self._bundle(self.user_apps, "Cursor.app")
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(str(app), result["install_path"])
        self.assertEqual("1.7.0", result["version"])

    def test_machine_wide_still_detected_and_preferred(self):
        machine = self._bundle(self.machine_apps, "Cursor.app")
        self._bundle(self.user_apps, "Cursor.app")
        self.assertEqual(str(machine), self._detect()["install_path"])

    def test_absent_everywhere_still_none(self):
        self.assertIsNone(self._detect())


class TestWindsurfUserApplications(_UserApplicationsCase):
    def setUp(self):
        super().setUp()
        from scripts.coding_discovery_tools.macos.windsurf import windsurf as mod
        self.mod = mod
        self.detector = mod.MacOSWindsurfDetector()
        self.detector.user_home = self.user_home

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps), \
             patch.object(self.detector, "DEFAULT_APP_PATH", self.machine_apps / "Windsurf.app"), \
             patch.object(self.mod, "run_command", return_value="1.2.3"):
            return self.detector.detect()

    def test_user_local_install_detected(self):
        app = self._bundle(self.user_apps, "Windsurf.app")
        self.assertEqual(str(app), self._detect()["install_path"])

    def test_absent_everywhere_still_none(self):
        self.assertIsNone(self._detect())


class TestAntigravityUserApplications(_UserApplicationsCase):
    def setUp(self):
        super().setUp()
        from scripts.coding_discovery_tools.macos.antigravity import antigravity as mod
        self.mod = mod
        self.detector = mod.MacOSAntigravityDetector()
        self.detector.user_home = self.user_home

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps), \
             patch.object(self.detector, "POSSIBLE_APP_PATHS",
                          [self.machine_apps / "Antigravity.app"]), \
             patch.object(self.mod, "run_command", return_value="0.9.0"):
            return self.detector.detect()

    def test_user_local_install_detected(self):
        app = self._bundle(self.user_apps, "Antigravity.app")
        self.assertEqual(str(app), self._detect()["install_path"])

    def test_absent_everywhere_still_none(self):
        self.assertIsNone(self._detect())


class TestReplitUserApplications(_UserApplicationsCase):
    def setUp(self):
        super().setUp()
        from scripts.coding_discovery_tools.macos.replit import replit as mod
        self.mod = mod
        self.detector = mod.MacOSReplitDetector()
        self.detector.user_home = self.user_home

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps), \
             patch.object(self.detector, "APPLICATION_PATH", self.machine_apps / "Replit.app"), \
             patch.object(self.mod, "run_command", return_value="1.8.0"):
            return self.detector.detect()

    def test_user_local_install_detected(self):
        app = self._bundle(self.user_apps, "Replit.app")
        self.assertEqual(str(app), self._detect()["install_path"])

    def test_absent_everywhere_still_none(self):
        self.assertIsNone(self._detect())


class TestCopilotBuiltinExtensionRoots(unittest.TestCase):
    """The bundled Copilot lives inside the .app, so its location decides."""

    def setUp(self):
        from scripts.coding_discovery_tools.macos.github_copilot import detect_copilot as mod
        self.mod = mod
        self.tmp = tempfile.TemporaryDirectory()
        self.user_home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_user_local_bundle_root_included(self):
        roots = self.mod._app_extension_roots(self.user_home)
        self.assertIn(
            self.user_home / "Applications" / "Visual Studio Code.app"
            / "Contents" / "Resources" / "app" / "extensions",
            roots,
        )

    def test_machine_wide_roots_still_present(self):
        roots = self.mod._app_extension_roots(self.user_home)
        for root in self.mod._VSCODE_APP_EXTENSION_ROOTS:
            self.assertIn(root, roots)

    def test_patched_non_machine_roots_do_not_raise(self):
        """relative_to() must not raise on a tmp root a test patched in."""
        with patch.object(self.mod, "_VSCODE_APP_EXTENSION_ROOTS", [Path("/tmp/x/extensions")]):
            self.assertEqual([Path("/tmp/x/extensions")],
                             self.mod._app_extension_roots(self.user_home))


if __name__ == "__main__":
    unittest.main()
