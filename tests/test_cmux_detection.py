"""cmux is a layer in front of the agents, and worth reporting in its own right.

It runs each agent in a pane behind a wrapper of its own, and ``cmux hooks
setup`` writes its hooks into Claude Code, Codex, OpenCode and Gemini -- so a
device running it has something between the agent and us. Until now it was
invisible: the only trace in production was the per-pane wrappers being
miscounted as installs of the agents themselves, which the shim fix stops.

The bundle fixtures are real, built in a temp dir with ``MACHINE_APPS_DIR``
pointed at it, and the ``Info.plist`` written with ``plistlib`` -- so the
version and identifier are genuinely read rather than mocked.
"""
import os
import plistlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.cmux.cmux import (
    CMUX_BUNDLE_ID,
    MacOSCmuxDetector,
)

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"


class CmuxBundleCase(unittest.TestCase):
    """A temp world with a machine Applications dir and a scanned user's home."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.machine_apps = self.root / "Applications"
        self.machine_apps.mkdir()
        self.home = self.root / "home"
        (self.home / "Applications").mkdir(parents=True)

    def bundle(self, apps_dir, name, version="0.3.1", identifier=CMUX_BUNDLE_ID):
        """A real bundle: Info.plist written as a plist, not as text."""
        app = apps_dir / name
        contents = app / "Contents"
        contents.mkdir(parents=True)
        payload = {}
        if version is not None:
            payload["CFBundleShortVersionString"] = version
        if identifier is not None:
            payload["CFBundleIdentifier"] = identifier
        with open(contents / "Info.plist", "wb") as fh:
            plistlib.dump(payload, fh, fmt=plistlib.FMT_BINARY)
        return app

    def detect(self):
        """Run the detector with the temp world standing in for the real one."""
        det = MacOSCmuxDetector()
        det.user_home = self.home
        paths = [self.machine_apps / p.name for p in MacOSCmuxDetector.APP_PATHS]
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps), \
                patch.object(MacOSCmuxDetector, "APP_PATHS", paths):
            return det.detect()


class TheBundleIsWhatCounts(CmuxBundleCase):

    def test_an_installed_bundle_is_reported_with_its_real_version(self):
        app = self.bundle(self.machine_apps, "cmux.app", version="0.4.2")
        found = self.detect()
        self.assertEqual("cmux", found["name"])
        self.assertEqual(str(app), found["install_path"])
        self.assertEqual("0.4.2", found["version"])

    def test_nothing_is_reported_when_no_bundle_exists(self):
        self.assertIsNone(self.detect())

    def test_an_install_under_the_users_applications_is_found(self):
        """Where a non-admin install lands, and the path this detector adds."""
        app = self.bundle(self.home / "Applications", "cmux.app")
        found = self.detect()
        self.assertIsNotNone(found)
        self.assertEqual(str(app), found["install_path"])

    def test_a_missing_version_is_reported_as_unknown(self):
        """``None`` would travel to the backend as a JSON null; the report path
        and ``is_unknown_version`` already treat "Unknown" as the empty one."""
        self.bundle(self.machine_apps, "cmux.app", version=None)
        self.assertEqual("Unknown", self.detect()["version"])


class EveryChannelIsRecognised(CmuxBundleCase):
    """Upstream ships several channels, each its own bundle beside the others."""

    def test_each_channel_is_found_on_its_own(self):
        for name, identifier in (
                ("cmux.app", CMUX_BUNDLE_ID),
                ("cmux NIGHTLY.app", CMUX_BUNDLE_ID + ".nightly"),
                ("cmux NEXT.app", CMUX_BUNDLE_ID + ".nightly.cmux-next"),
                ("cmux RC.app", CMUX_BUNDLE_ID + ".rc"),
        ):
            with self.subTest(channel=name):
                shutil.rmtree(self.machine_apps)
                self.machine_apps.mkdir()
                app = self.bundle(self.machine_apps, name, identifier=identifier)
                found = self.detect()
                self.assertIsNotNone(found, name)
                self.assertEqual(str(app), found["install_path"])

    def test_stable_wins_when_a_pre_release_sits_beside_it(self):
        stable = self.bundle(self.machine_apps, "cmux.app")
        self.bundle(self.machine_apps, "cmux NIGHTLY.app",
                    identifier=CMUX_BUNDLE_ID + ".nightly")
        self.assertEqual(str(stable), self.detect()["install_path"])

    def test_the_nightly_name_is_the_one_upstream_ships(self):
        """Upstream names it ``cmux NIGHTLY``. A differently-cased guess still
        resolves on a case-insensitive volume and records the wrong path, so the
        spelling is pinned here rather than left to the filesystem."""
        self.assertIn("cmux NIGHTLY.app",
                      [p.name for p in MacOSCmuxDetector.APP_PATHS])


class AnotherProductNamedCmuxIsNotReported(CmuxBundleCase):
    """``cmux`` is also the name of unrelated projects, one of which is itself a
    desktop app. The bundle identifier is what settles which one is installed."""

    def test_a_foreign_bundle_of_the_same_name_is_ignored(self):
        self.bundle(self.machine_apps, "cmux.app", identifier="com.coder.cmux")
        self.assertIsNone(self.detect())

    def test_a_bundle_with_no_identifier_is_ignored(self):
        self.bundle(self.machine_apps, "cmux.app", identifier=None)
        self.assertIsNone(self.detect())

    def test_a_suffix_of_the_identifier_is_still_cmux(self):
        app = self.bundle(self.machine_apps, "cmux.app",
                          identifier=CMUX_BUNDLE_ID + ".somefuturechannel")
        self.assertEqual(str(app), self.detect()["install_path"])


class ResidueIsNotAnInstall(CmuxBundleCase):
    """The traces that outlive the app must not report it.

    This is the false positive the Antigravity detector already had to remove
    when it dropped its ``~/.antigravity`` fallback. Each case plants the
    residue, asserts it is there, and points ``TMPDIR`` at the planted root --
    so a detector that grew a residue or ``$TMPDIR`` fallback would find it and
    the case would fail.
    """

    def _detect_with_residue(self):
        settings = self.home / ".config" / "cmux" / "cmux.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        snapshots = self.home / "Library" / "Application Support" / "cmux"
        snapshots.mkdir(parents=True)
        hooks = self.home / ".cmuxterm"
        hooks.mkdir()
        wrapper = self.root / "tmp" / "cmux-cli-shims" / "panel-1" / "claude"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n")
        for p in (settings, snapshots, hooks, wrapper):
            self.assertTrue(p.exists(), p)
        with patch.dict(os.environ, {"TMPDIR": str(self.root / "tmp")}):
            return self.detect()

    def test_residue_and_a_pane_wrapper_report_nothing(self):
        self.assertIsNone(self._detect_with_residue())


class ARedirectedPlistCannotStallTheScan(CmuxBundleCase):
    """The bundle can sit under a user-writable home during a root scan, so the
    plist is attacker-controlled there. ``read_bundle_version`` lstats it and
    requires a regular file; ``defaults read`` would block on a FIFO until the
    version timeout, once per user.
    """

    def test_a_plist_symlinked_to_a_fifo_is_refused_at_once(self):
        app = self.home / "Applications" / "cmux.app"
        (app / "Contents").mkdir(parents=True)
        fifo = self.root / "blocks"
        try:
            os.mkfifo(fifo)
        except (AttributeError, NotImplementedError, OSError) as exc:
            self.skipTest(f"cannot create a FIFO here: {exc}")
        os.symlink(fifo, app / "Contents" / "Info.plist")

        started = time.monotonic()
        found = self.detect()
        elapsed = time.monotonic() - started

        # Nothing is reported: a bundle whose identity cannot be read safely is
        # not treated as cmux.
        self.assertIsNone(found)
        # And it came back immediately rather than waiting out the timeout.
        self.assertLess(elapsed, 5, f"took {elapsed:.1f}s; a read blocked")


class ItIsWiredIntoTheScan(unittest.TestCase):
    """A detector nothing calls finds nothing."""

    def test_the_macos_scan_includes_it(self):
        names = [d.tool_name for d in
                 ToolDetectorFactory.create_all_tool_detectors("Darwin")]
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
