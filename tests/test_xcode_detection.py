"""Gate tests for the macOS Xcode coding intelligence detector.

Xcode ships on most developer Macs, so the app bundle alone must not produce a
tool row — detection AND-requires the per-user CodingAssistant tree that Apple
documents for agent config, MCP servers and skills. A denied read of that tree
must raise rather than report the clean absence that would permit a prune.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.xcode import MacOSXcodeDetector
from scripts.coding_discovery_tools.macos.xcode import xcode as xcode_mod

_ASSISTANT = Path("Library") / "Developer" / "Xcode" / "CodingAssistant"


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
        # Neither xcode-select nor mdfind may answer for the scanner's own Mac.
        patches = [
            patch.object(xcode_mod, "MACHINE_APPS_DIR", self.apps),
            patch.object(xcode_mod, "_active_developer_bundle", lambda: None),
            patch.object(xcode_mod, "run_command", lambda *a, **k: None),
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

        row = self._detector().detect()

        self.assertEqual(row["version"], "26.2")

    def test_all_agent_dirs_are_probed(self):
        _make_bundle(self.apps / "Xcode.app")
        for name in ("ClaudeAgentConfig", "codex", "gemini"):
            (self.home / _ASSISTANT / name).mkdir(parents=True)

        self._detector().detect()

        self.assertIn("agents:ClaudeAgentConfig+codex+gemini", utils_mod.xcode_probes())

    def test_denied_coding_assistant_raises_instead_of_reporting_absent(self):
        _make_bundle(self.apps / "Xcode.app")
        (self.home / _ASSISTANT).mkdir(parents=True)
        denied = self.home / "Library" / "Developer" / "Xcode"
        denied.chmod(0o000)
        self.addCleanup(denied.chmod, 0o755)

        with self.assertRaises(PermissionError):
            self._detector().detect()
        self.assertIn("coding_assistant:unreadable", utils_mod.xcode_probes())


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
