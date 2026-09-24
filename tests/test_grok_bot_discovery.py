"""Detection and config-extraction tests for Grok Bot (macOS).

Grok Bot keeps its skills and MCP server definitions on its cloud computer, so
the device-side footprint is the app bundle plus ``~/.grokbot/settings.json``.
The local-exec daemon's credential files sit beside settings.json and must
never reach the payload.

Bundles and homes are built in temp dirs; no test touches ``/Applications`` or
the real home.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.grok_bot.grok_bot import MacOSGrokBotDetector
from scripts.coding_discovery_tools.macos.grok_bot.grok_bot_rules_extractor import (
    MacOSGrokBotRulesExtractor,
)

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"
_EXT_MOD = "scripts.coding_discovery_tools.macos.grok_bot.grok_bot_rules_extractor"


class TestMacOSGrokBotDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.machine_apps = root / "Applications"
        self.machine_apps.mkdir()
        self.user_home = root / "Users" / "alice"
        (self.user_home / "Applications").mkdir(parents=True)
        self.detector = MacOSGrokBotDetector()
        self.detector.user_home = self.user_home
        self.detector.APP_PATH = self.machine_apps / "Grok Bot.app"

    def tearDown(self):
        self.tmp.cleanup()

    def _bundle(self, parent: Path, version: str = "0.58.0") -> Path:
        app = parent / "Grok Bot.app"
        plist = app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        with open(plist, "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
        return app

    def test_machine_wide_bundle_detected_with_version(self):
        app = self._bundle(self.machine_apps)
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps):
            result = self.detector.detect()
        self.assertEqual(
            result, {"name": "Grok Bot", "version": "0.58.0", "install_path": str(app)}
        )

    def test_user_applications_bundle_detected(self):
        app = self._bundle(self.user_home / "Applications", version="0.59.1")
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(app))
        self.assertEqual(result["version"], "0.59.1")

    def test_no_bundle_not_detected(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps):
            self.assertIsNone(self.detector.detect())


class TestMacOSGrokBotConfigExtraction(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "alice"
        self.data_dir = self.home / ".grokbot"
        self.data_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _extract(self):
        with patch(f"{_EXT_MOD}.is_running_as_root", return_value=False), \
                patch("pathlib.Path.home", return_value=self.home):
            return MacOSGrokBotRulesExtractor().extract_all_grok_bot_rules()

    def test_settings_json_collected_as_user_scope(self):
        body = '{\n  "localToolPermission": "ask",\n  "mcpBoxServers": ["srv-1"]\n}'
        (self.data_dir / "settings.json").write_text(body, encoding="utf-8")
        projects = self._extract()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(self.home))
        [rule] = projects[0]["rules"]
        self.assertEqual(rule["file_name"], "settings.json")
        self.assertEqual(rule["scope"], "user")
        self.assertEqual(rule["content"], body)

    def test_no_settings_json_reports_nothing(self):
        (self.data_dir / "local-exec-daemon.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._extract(), [])

    def test_daemon_credential_files_never_collected(self):
        (self.data_dir / "settings.json").write_text("{}", encoding="utf-8")
        for name in ("local-exec-daemon-credential.json", "local-exec-daemon-connection.json"):
            (self.data_dir / name).write_text('{"data": "sealed"}', encoding="utf-8")
        projects = self._extract()
        names = [r["file_name"] for p in projects for r in p["rules"]]
        self.assertEqual(names, ["settings.json"])

    def test_secret_values_redacted(self):
        (self.data_dir / "settings.json").write_text(
            '{"apiKey": "sk-live-abc123", "localToolPermission": "ask"}', encoding="utf-8"
        )
        [project] = self._extract()
        content = project["rules"][0]["content"]
        self.assertNotIn("sk-live-abc123", content)
        self.assertIn('"localToolPermission": "ask"', content)


class TestGrokBotFactories(unittest.TestCase):
    def test_detector_registered_on_macos_only(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        for os_name, expected in (("Darwin", True), ("Linux", False), ("Windows", False)):
            names = [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)]
            self.assertEqual("Grok Bot" in names, expected, os_name)

    def test_rules_extractor_factory_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import GrokBotRulesExtractorFactory

        self.assertIsNotNone(GrokBotRulesExtractorFactory.create("Darwin"))
        self.assertIsNone(GrokBotRulesExtractorFactory.create("Linux"))
        self.assertIsNone(GrokBotRulesExtractorFactory.create("Windows"))


if __name__ == "__main__":
    unittest.main()
