"""Gate tests for the macOS GitHub Copilot for Xcode detector.

The bundle is a dedicated Copilot app, so unlike Xcode it IS evidence of an AI
tool. It is machine-wide though, so a per-user marker decides which profiles on
a shared Mac get a row rather than attributing one install to every account.

A denied read must reach the anomaly path: only a clean absence may let the
backend prune a live install.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.macos_extraction_helpers as helpers_mod
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.github_copilot_xcode import MacOSGitHubCopilotXcodeDetector
from scripts.coding_discovery_tools.macos.github_copilot_xcode import github_copilot_xcode as copilot_mod

_APP = "GitHub Copilot for Xcode.app"
_LOGS = Path("Library") / "Logs" / "GitHubCopilot"


class GitHubCopilotXcodeDetectionTests(unittest.TestCase):
    def setUp(self):
        utils_mod._copilot_xcode_probes.clear()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "Users" / "dev"
        self.home.mkdir(parents=True)
        self.apps = root / "Applications"
        self.apps.mkdir()
        patches = [
            patch.object(copilot_mod, "MACHINE_APPS_DIR", self.apps),
            patch.object(copilot_mod, "APP_BUNDLE", self.apps / _APP),
            patch.object(helpers_mod, "MACHINE_APPS_DIR", self.apps),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _detector(self):
        detector = MacOSGitHubCopilotXcodeDetector()
        detector.user_home = self.home
        return detector

    def _install_bundle(self, version="0.45.0", under=None):
        app = (under or self.apps) / _APP
        contents = app / "Contents"
        contents.mkdir(parents=True)
        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
        return app

    def _install_marker(self, relative=_LOGS):
        marker = self.home / relative
        marker.parent.mkdir(parents=True, exist_ok=True)
        if marker.suffix == ".plist":
            marker.write_text("")
        else:
            marker.mkdir()
        return marker

    def test_bundle_and_user_marker_detects(self):
        app = self._install_bundle()
        self._install_marker()

        result = self._detector().detect()

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "GitHub Copilot (Xcode)")
        self.assertEqual(result["version"], "0.45.0")
        # install_path is part of the manifest key, so it tracks the bundle
        # rather than whichever marker happened to match.
        self.assertEqual(result["install_path"], str(app))

    def test_bundle_without_user_marker_is_not_a_tool(self):
        self._install_bundle()

        self.assertIsNone(self._detector().detect())
        self.assertIn("user_state:absent", utils_mod.copilot_xcode_probes())

    def test_user_marker_without_bundle_is_not_a_tool(self):
        self._install_marker()

        self.assertIsNone(self._detector().detect())
        self.assertIn("bundle:absent", utils_mod.copilot_xcode_probes())

    def test_any_marker_satisfies_the_gate(self):
        self._install_bundle()
        for relative in copilot_mod.USER_MARKERS:
            with self.subTest(marker=str(relative)):
                utils_mod._copilot_xcode_probes.clear()
                marker = self._install_marker(relative)
                self.assertIsNotNone(self._detector().detect())
                if marker.is_dir():
                    marker.rmdir()
                else:
                    marker.unlink()

    def test_user_install_under_home_applications_counts(self):
        user_apps = self.home / "Applications"
        user_apps.mkdir()
        self._install_bundle(under=user_apps)
        self._install_marker()

        result = self._detector().detect()

        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(user_apps / _APP))

    def test_denied_marker_read_raises_instead_of_reporting_absence(self):
        self._install_bundle()

        with patch.object(copilot_mod, "_marker_state", return_value="unreadable"):
            with self.assertRaises(PermissionError):
                self._detector().detect()

    def test_another_users_marker_is_not_attributed(self):
        self._install_bundle()
        other = self.home.parent / "other"
        (other / _LOGS).mkdir(parents=True)

        self.assertIsNone(self._detector().detect())

    def test_version_missing_does_not_block_detection(self):
        app = self.apps / _APP
        (app / "Contents").mkdir(parents=True)
        self._install_marker()

        result = self._detector().detect()

        self.assertIsNotNone(result)
        self.assertIsNone(result["version"])

    def test_factory_registers_the_detector_on_macos_only(self):
        self.assertIsInstance(
            ToolDetectorFactory.create_copilot_xcode_detector("Darwin"),
            MacOSGitHubCopilotXcodeDetector,
        )
        self.assertIsNone(ToolDetectorFactory.create_copilot_xcode_detector("Windows"))
        self.assertIsNone(ToolDetectorFactory.create_copilot_xcode_detector("Linux"))

    def test_probe_is_a_queryable_sentry_tag(self):
        self.assertIn("copilot_xcode_probe", utils_mod._SENTRY_TAG_KEYS)


if __name__ == "__main__":
    unittest.main()
