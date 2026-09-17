"""Gate tests for the macOS Xcode coding intelligence detector.

Xcode ships on most developer Macs, so the app bundle alone must not produce a
tool row — detection AND-requires the per-user CodingAssistant tree that Apple
documents for agent config, MCP servers and skills.

The gate's failure modes matter as much as its success: a denied read and a
failed probe must both reach the anomaly path, because only a clean absence may
let the backend prune a live install.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.macos_extraction_helpers as helpers_mod
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.xcode import MacOSXcodeDetector
from scripts.coding_discovery_tools.macos.xcode import xcode as xcode_mod

_ASSISTANT = Path("Library") / "Developer" / "Xcode" / "CodingAssistant"
_MOD = "scripts.coding_discovery_tools.macos.xcode.xcode"


def _make_bundle(app: Path, version: str = "26.1") -> None:
    contents = app / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": version}, fh)


class XcodeDetectionTests(unittest.TestCase):
    def setUp(self):
        utils_mod._xcode_probes.clear()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "Users" / "dev"
        self.home.mkdir(parents=True)
        self.apps = root / "Applications"
        self.apps.mkdir()
        # Both probes answer "ran, found nothing" unless a test says otherwise.
        patches = [
            patch.object(xcode_mod, "MACHINE_APPS_DIR", self.apps),
            patch.object(helpers_mod, "MACHINE_APPS_DIR", self.apps),
            patch(f"{_MOD}._active_developer_bundle", lambda: (None, True)),
            patch(f"{_MOD}._spotlight_candidates", lambda home: ([], True)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _detector(self):
        detector = MacOSXcodeDetector()
        detector.user_home = self.home
        return detector

    def test_bundle_without_coding_assistant_is_not_a_tool(self):
        _make_bundle(self.apps / "Xcode.app")
        self.assertIsNone(self._detector().detect())
        self.assertIn("coding_assistant:absent", utils_mod.xcode_probes())

    def test_coding_assistant_and_bundle_detects(self):
        _make_bundle(self.apps / "Xcode.app")
        (self.home / _ASSISTANT / "ClaudeAgentConfig").mkdir(parents=True)

        row = self._detector().detect()

        self.assertEqual(row["name"], "Xcode Coding Intelligence")
        self.assertEqual(row["version"], "26.1")
        self.assertEqual(row["install_path"], str(self.home / _ASSISTANT))
        self.assertIn("agents:ClaudeAgentConfig", utils_mod.xcode_probes())

    def test_coding_assistant_without_bundle_reports_nothing(self):
        (self.home / _ASSISTANT / "codex").mkdir(parents=True)
        self.assertIsNone(self._detector().detect())
        self.assertIn("bundle:absent", utils_mod.xcode_probes())

    def test_user_local_application_bundle_is_found(self):
        _make_bundle(self.home / "Applications" / "Xcode.app", "26.2")
        (self.home / _ASSISTANT / "gemini").mkdir(parents=True)

        self.assertEqual(self._detector().detect()["version"], "26.2")

    def test_unknown_agent_dirs_are_counted_never_named(self):
        _make_bundle(self.apps / "Xcode.app")
        for name in ("ClaudeAgentConfig", "codex", "s3-creds-backup"):
            (self.home / _ASSISTANT / name).mkdir(parents=True)

        self._detector().detect()

        self.assertIn("agents:ClaudeAgentConfig+codex+other:1", utils_mod.xcode_probes())
        self.assertNotIn("s3-creds-backup", ",".join(utils_mod.xcode_probes()))

    def test_denied_coding_assistant_raises_instead_of_reporting_absent(self):
        _make_bundle(self.apps / "Xcode.app")
        (self.home / _ASSISTANT).mkdir(parents=True)

        with patch(f"{_MOD}.dir_state", return_value="unreadable"):
            with self.assertRaises(PermissionError):
                self._detector().detect()
        self.assertIn("coding_assistant:unreadable", utils_mod.xcode_probes())

    def test_denied_agent_listing_is_not_reported_as_none(self):
        _make_bundle(self.apps / "Xcode.app")
        (self.home / _ASSISTANT).mkdir(parents=True)

        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            self._detector().detect()

        self.assertIn("agents:unreadable", utils_mod.xcode_probes())

    def test_failed_probe_raises_rather_than_reporting_absent(self):
        (self.home / _ASSISTANT / "codex").mkdir(parents=True)

        with patch(f"{_MOD}._spotlight_candidates", lambda home: ([], False)):
            with self.assertRaises(PermissionError):
                self._detector().detect()
        self.assertIn("bundle:unknown", utils_mod.xcode_probes())

    def test_stale_tree_with_no_xcode_is_a_clean_absence(self):
        """Xcode removed, CodingAssistant left behind: the probes ran and found
        nothing, so this must not raise and strand the whole scan."""
        (self.home / _ASSISTANT / "codex").mkdir(parents=True)

        self.assertIsNone(self._detector().detect())
        self.assertIn("bundle:absent", utils_mod.xcode_probes())

    def test_version_reads_on_every_platform(self):
        """O_NOFOLLOW/O_NONBLOCK are POSIX-only; resolving them unguarded made the
        read raise AttributeError and silently return no version off macOS."""
        app = self.apps / "Xcode.app"
        _make_bundle(app, "26.3")
        self.assertEqual(xcode_mod._read_bundle_version(app), "26.3")

    def test_empty_search_is_absence_but_a_dead_binary_is_unknown(self):
        real = utils_mod.run_command_status
        self.assertEqual(real(["mdfind", "kMDItemCFBundleIdentifier == 'com.nope.nothing'"]),
                         (None, True))
        self.assertEqual(real(["definitely-not-a-binary-xyz"]), (None, False))

    def test_redirected_coding_assistant_is_out_of_scope(self):
        _make_bundle(self.apps / "Xcode.app")

        with patch(f"{_MOD}.path_in_scope", return_value=False):
            self.assertIsNone(self._detector().detect())
        self.assertIn("coding_assistant:redirected", utils_mod.xcode_probes())


class XcodeFactoryTests(unittest.TestCase):
    def test_detector_is_macos_only(self):
        self.assertIsNotNone(ToolDetectorFactory.create_xcode_detector("Darwin"))
        self.assertIsNone(ToolDetectorFactory.create_xcode_detector("Windows"))
        self.assertIsNone(ToolDetectorFactory.create_xcode_detector("Linux"))

    def test_registered_only_in_the_macos_detector_list(self):
        def names(os_name):
            return [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)]

        self.assertIn("Xcode Coding Intelligence", names("Darwin"))
        self.assertNotIn("Xcode Coding Intelligence", names("Windows"))
        self.assertNotIn("Xcode Coding Intelligence", names("Linux"))


if __name__ == "__main__":
    unittest.main()
