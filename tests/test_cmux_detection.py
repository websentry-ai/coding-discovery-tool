"""cmux is a layer in front of the agents, and worth reporting in its own right.

It runs each agent behind a wrapper of its own and ``cmux hooks setup`` writes
its hooks into Claude Code, Codex, OpenCode and Gemini -- so a device running it
has something between the agent and us. Until now it was invisible: the only
trace in production was the per-pane wrappers being miscounted as installs of
the agents themselves, which the shim fix stops reporting.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.cmux.cmux import MacOSCmuxDetector


class TheBundleIsWhatCounts(unittest.TestCase):
    """Gated on the app bundle, which uninstall removes."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.apps = Path(self.root) / "Applications"
        self.apps.mkdir()

    def _bundle(self, name="cmux.app", version="0.3.1"):
        app = self.apps / name
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_text("<plist/>")
        return app

    def _detect(self, paths, version_output="0.3.1"):
        det = MacOSCmuxDetector()
        with patch.object(MacOSCmuxDetector, "POSSIBLE_APP_PATHS", paths), \
                patch("scripts.coding_discovery_tools.macos.cmux.cmux.run_command",
                      return_value=version_output):
            return det.detect()

    def test_an_installed_bundle_is_reported(self):
        app = self._bundle()
        found = self._detect([app])
        self.assertIsNotNone(found)
        self.assertEqual("cmux", found["name"])
        self.assertEqual(str(app), found["install_path"])
        self.assertEqual("0.3.1", found["version"])

    def test_nothing_is_reported_when_the_bundle_is_absent(self):
        self.assertIsNone(self._detect([self.apps / "cmux.app"]))

    def test_nightly_is_reported_when_it_is_the_only_one(self):
        nightly = self._bundle("cmux Nightly.app")
        found = self._detect([self.apps / "cmux.app", nightly])
        self.assertIsNotNone(found)
        self.assertEqual(str(nightly), found["install_path"])

    def test_stable_wins_when_both_are_installed(self):
        stable = self._bundle("cmux.app")
        nightly = self._bundle("cmux Nightly.app")
        found = self._detect([stable, nightly])
        self.assertEqual(str(stable), found["install_path"])

    def test_a_missing_version_does_not_lose_the_install(self):
        app = self._bundle()
        found = self._detect([app], version_output="")
        self.assertIsNotNone(found)
        self.assertIsNone(found["version"])


class ResidueIsNotAnInstall(unittest.TestCase):
    """The traces that outlive the app must not report it.

    This is the false positive the Antigravity detector already had to remove:
    a config directory survives uninstall, so gating on one reports a tool the
    user no longer has. Each case plants the residue and asserts it is there,
    so the case cannot pass by having created nothing.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.home = self.root / "home"
        self.home.mkdir()

    def _detect_with_no_bundle(self):
        det = MacOSCmuxDetector()
        det.user_home = self.home
        absent = self.home / "Applications" / "cmux.app"
        self.assertFalse(absent.exists(), "the bundle must be absent for this case")
        with patch.object(MacOSCmuxDetector, "POSSIBLE_APP_PATHS", [absent]):
            return det.detect()

    def test_config_and_session_dirs_alone_report_nothing(self):
        settings = self.home / ".config" / "cmux" / "cmux.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        snapshots = self.home / "Library" / "Application Support" / "cmux"
        snapshots.mkdir(parents=True)
        hooks = self.home / ".cmuxterm"
        hooks.mkdir()
        for p in (settings, snapshots, hooks):
            self.assertTrue(p.exists(), p)
        self.assertIsNone(self._detect_with_no_bundle())

    def test_a_pane_wrapper_in_temp_alone_reports_nothing(self):
        """The per-pane wrappers belong to a pane, not to an installation."""
        wrapper = self.root / "tmp" / "cmux-cli-shims" / "panel-1" / "claude"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n")
        self.assertTrue(wrapper.exists())
        self.assertIsNone(self._detect_with_no_bundle())


class ItIsWiredIntoTheScan(unittest.TestCase):
    """A detector nothing calls finds nothing."""

    def test_the_macos_scan_includes_it(self):
        names = [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors("Darwin")]
        self.assertIn("cmux", names)

    def test_it_is_not_claimed_on_platforms_it_does_not_ship_on(self):
        for os_name in ("Windows", "Linux"):
            with self.subTest(os=os_name):
                self.assertIsNone(ToolDetectorFactory.create_cmux_detector(os_name))
                names = [d.tool_name for d in
                         ToolDetectorFactory.create_all_tool_detectors(os_name)]
                self.assertNotIn("cmux", names)


if __name__ == "__main__":
    unittest.main()
