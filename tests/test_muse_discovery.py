"""Detection and extraction tests for Muse (Meta's agent app) and Muse Code (macOS).

Muse keeps skills on Meta's cloud computer and has no configurable MCP servers,
so only its bundle is reported — and only when the bundle id is Meta's, since
an unrelated whiteboard app also ships as ``Muse.app``.

Muse Code is local: ``~/.config/muse/settings.json`` (config + ``mcpServers``)
and personal skills under ``~/.config/muse/skills``. ``auth.json`` beside them
holds OAuth tokens and must never be read.

Bundles and homes are built in temp dirs; no test touches ``/Applications`` or
the real home.
"""

import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.muse.muse import MacOSMuseDetector
from scripts.coding_discovery_tools.macos.muse_code.muse_code import MacOSMuseCodeDetector
from scripts.coding_discovery_tools.macos.muse_code.mcp_config_extractor import (
    MacOSMuseCodeMCPConfigExtractor,
)
from scripts.coding_discovery_tools.macos.muse_code.muse_code_rules_extractor import (
    MacOSMuseCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos.muse_code.skills_extractor import (
    MacOSMuseCodeSkillsExtractor,
)

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"
_RULES_MOD = "scripts.coding_discovery_tools.macos.muse_code.muse_code_rules_extractor"
_SKILLS_MOD = "scripts.coding_discovery_tools.macos.muse_code.skills_extractor"
_MCP_MOD = "scripts.coding_discovery_tools.macos.muse_code.mcp_config_extractor"


def _bundle(parent: Path, bundle_id: str, version: str = "3.0") -> Path:
    app = parent / "Muse.app"
    plist = app / "Contents" / "Info.plist"
    plist.parent.mkdir(parents=True)
    with open(plist, "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, fh)
    return app


class TestMacOSMuseDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.machine_apps = root / "Applications"
        self.machine_apps.mkdir()
        self.user_home = root / "Users" / "alice"
        (self.user_home / "Applications").mkdir(parents=True)
        self.detector = MacOSMuseDetector()
        self.detector.user_home = self.user_home
        self.detector.APP_PATH = self.machine_apps / "Muse.app"

    def tearDown(self):
        self.tmp.cleanup()

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps):
            return self.detector.detect()

    def test_meta_bundle_detected_with_version(self):
        app = _bundle(self.machine_apps, "com.meta.endo")
        self.assertEqual(self._detect(), {"name": "Muse", "version": "3.0", "install_path": str(app)})

    def test_unrelated_muse_app_not_detected(self):
        _bundle(self.machine_apps, "com.museapp.macos")
        self.assertIsNone(self._detect())

    def test_user_bundle_found_behind_unrelated_machine_wide_muse(self):
        """The whiteboard Muse.app in /Applications must not hide Meta's in ~/Applications."""
        _bundle(self.machine_apps, "com.museapp.macos")
        app = _bundle(self.user_home / "Applications", "com.meta.endo")
        self.assertEqual(self._detect()["install_path"], str(app))

    def test_user_applications_bundle_detected(self):
        app = _bundle(self.user_home / "Applications", "com.meta.endo")
        self.assertEqual(self._detect()["install_path"], str(app))


class TestMacOSMuseCodeDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "alice"
        self.bin_dir = self.home / ".local" / "bin"
        self.bin_dir.mkdir(parents=True)
        self.detector = MacOSMuseCodeDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        self.tmp.cleanup()

    def _launcher(self):
        launcher = self.bin_dir / "muse"
        launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        launcher.chmod(0o755)
        return launcher

    def test_launcher_and_version_marker_detected(self):
        launcher = self._launcher()
        (self.bin_dir / ".muse-version").write_text("1.3.0-R3401.1\n", encoding="utf-8")
        self.assertEqual(
            self.detector.detect(),
            {"name": "Muse Code", "version": "1.3.0-R3401.1", "install_path": str(launcher)},
        )

    def test_bare_muse_binary_not_detected(self):
        """``muse`` is a common name: without the installer's marker it is not Muse Code."""
        self._launcher()
        self.assertIsNone(self.detector.detect())

    def test_nothing_installed_not_detected(self):
        self.assertIsNone(self.detector.detect())


class TestMacOSMuseCodeExtraction(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "alice"
        self.config_dir = self.home / ".config" / "muse"
        self.config_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _home_patches(self, module):
        return (
            patch(f"{module}.is_running_as_root", return_value=False),
            patch("pathlib.Path.home", return_value=self.home),
        )

    def _rules(self):
        root_patch, home_patch = self._home_patches(_RULES_MOD)
        with root_patch, home_patch:
            return MacOSMuseCodeRulesExtractor().extract_all_muse_code_rules()

    def _mcp(self):
        with patch(f"{_MCP_MOD}.is_running_as_root", return_value=False), \
                patch("pathlib.Path.home", return_value=self.home), \
                patch(f"{_MCP_MOD}.transform_mcp_servers_to_array",
                      side_effect=lambda servers: [{"name": n, **c} for n, c in servers.items()]):
            return MacOSMuseCodeMCPConfigExtractor().extract_mcp_config()

    def _skills(self):
        root_patch, home_patch = self._home_patches(_SKILLS_MOD)
        with root_patch, home_patch:
            return MacOSMuseCodeSkillsExtractor().extract_all_skills()

    def test_settings_json_collected_as_user_scope(self):
        body = json.dumps({"schema_version": 1, "mcpServers": {}}, indent=2)
        (self.config_dir / "settings.json").write_bytes(body.encode("utf-8"))
        [project] = self._rules()
        self.assertEqual(project["project_root"], str(self.home))
        [rule] = project["rules"]
        self.assertEqual((rule["file_name"], rule["scope"], rule["content"]), ("settings.json", "user", body))

    def test_auth_json_never_collected(self):
        (self.config_dir / "settings.json").write_text("{}", encoding="utf-8")
        (self.config_dir / "auth.json").write_text('{"access_token": "secret"}', encoding="utf-8")
        names = [r["file_name"] for p in self._rules() for r in p["rules"]]
        self.assertEqual(names, ["settings.json"])

    def test_mcp_servers_keyed_on_home(self):
        body = {"schema_version": 1, "mcpServers": {"deepwiki": {"url": "https://mcp.deepwiki.com/mcp"}}}
        (self.config_dir / "settings.json").write_bytes(json.dumps(body).encode("utf-8"))
        result = self._mcp()
        self.assertEqual(
            result,
            {"projects": [{"path": str(self.home),
                           "mcpServers": [{"name": "deepwiki", "url": "https://mcp.deepwiki.com/mcp"}]}]},
        )

    def test_no_mcp_servers_returns_none(self):
        (self.config_dir / "settings.json").write_bytes(b'{"schema_version": 1}')
        self.assertIsNone(self._mcp())

    @unittest.skipIf(os.name == "nt", "POSIX links; the extractor is macOS-only")
    def test_linked_settings_json_not_read(self):
        """A linked settings.json must not smuggle another file out, via config or MCP."""
        other = self.config_dir / "auth.json"
        other.write_bytes(b'{"access_token": "secret", "mcpServers": {"x": {"url": "https://a"}}}')
        (self.config_dir / "settings.json").symlink_to(other)
        self.assertEqual(self._rules(), [])
        self.assertIsNone(self._mcp())
        (self.config_dir / "settings.json").unlink()
        os.link(other, self.config_dir / "settings.json")
        self.assertEqual(self._rules(), [])
        self.assertIsNone(self._mcp())

    @unittest.skipIf(not hasattr(os, "mkfifo"), "needs mkfifo")
    def test_fifo_settings_json_does_not_hang(self):
        os.mkfifo(self.config_dir / "settings.json")
        self.assertEqual(self._rules(), [])
        self.assertIsNone(self._mcp())

    def test_personal_skill_attributed_to_home(self):
        skill_dir = self.config_dir / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: review\n---\nReview.\n", encoding="utf-8")
        lock_dir = self.config_dir / "skills" / ".muse"
        lock_dir.mkdir()
        (lock_dir / "lock.json").write_text("{}", encoding="utf-8")
        result = self._skills()
        self.assertEqual(result["project_skills"], [])
        [skill] = result["user_skills"]
        self.assertEqual(skill["skill_name"], "review")
        self.assertEqual(skill["scope"], "user")
        self.assertEqual(skill["project_path"], str(self.home))

    def test_no_skills_dir_returns_empty(self):
        self.assertEqual(self._skills(), {"user_skills": [], "project_skills": []})


class TestMuseFactories(unittest.TestCase):
    def test_detectors_registered_on_macos_only(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        for os_name, expected in (("Darwin", True), ("Linux", False), ("Windows", False)):
            names = [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)]
            self.assertEqual("Muse" in names, expected, os_name)
            self.assertEqual("Muse Code" in names, expected, os_name)

    def test_muse_code_extractor_factories_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import (
            MuseCodeMCPConfigExtractorFactory,
            MuseCodeRulesExtractorFactory,
            MuseCodeSkillsExtractorFactory,
        )

        for factory in (
            MuseCodeRulesExtractorFactory,
            MuseCodeMCPConfigExtractorFactory,
            MuseCodeSkillsExtractorFactory,
        ):
            self.assertIsNotNone(factory.create("Darwin"), factory.__name__)
            self.assertIsNone(factory.create("Linux"), factory.__name__)
            self.assertIsNone(factory.create("Windows"), factory.__name__)


if __name__ == "__main__":
    unittest.main()
