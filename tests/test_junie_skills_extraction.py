"""
Unit tests for Junie rules/MCP extraction (macOS and Windows).

10 tests covering the critical paths:
- find_junie_project_root path resolution
- Detector: present/absent .junie dir, version from config
- RulesExtractor: global rule extraction
- MCPConfigExtractor: reads ~/.junie/mcp/mcp.json
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.macos.junie.junie import MacOSJunieDetector
from scripts.coding_discovery_tools.macos.junie.junie_rules_extractor import (
    MacOSJunieRulesExtractor,
    find_junie_project_root,
)
from scripts.coding_discovery_tools.macos.junie.mcp_config_extractor import MacOSJunieMCPConfigExtractor
from scripts.coding_discovery_tools.windows.junie.junie import WindowsJunieDetector
from scripts.coding_discovery_tools.windows.junie.junie_rules_extractor import WindowsJunieRulesExtractor
from scripts.coding_discovery_tools.windows.junie.mcp_config_extractor import WindowsJunieMCPConfigExtractor


def _write_mcp_json(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestFindJunieProjectRoot(unittest.TestCase):

    def test_project_level_rule_returns_project(self):
        rule = Path("/Users/test/myproject/.junie/guidelines.md")
        self.assertEqual(find_junie_project_root(rule), Path("/Users/test/myproject"))

    def test_user_level_rule_returns_home(self):
        rule = Path("/Users/test/.junie/global.md")
        self.assertEqual(find_junie_project_root(rule), Path("/Users/test"))


class TestMacOSJunieDetector(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_detects_when_junie_dir_exists(self, mock_home, _):
        mock_home.return_value = self.home
        (self.home / ".junie").mkdir()
        result = MacOSJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")

    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_returns_none_when_no_junie_dir(self, mock_home, _):
        mock_home.return_value = self.home
        self.assertIsNone(MacOSJunieDetector().detect())

    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_version_read_from_config_json(self, mock_home, _):
        mock_home.return_value = self.home
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "config.json").write_text(json.dumps({"version": "1.2.3"}))
        self.assertEqual(MacOSJunieDetector().detect()["version"], "1.2.3")


class TestMacOSJunieRulesExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("scripts.coding_discovery_tools.macos.junie.junie_rules_extractor.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie_rules_extractor.Path.home")
    def test_global_rule_extracted(self, mock_home, _):
        mock_home.return_value = self.home
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "guidelines.md").write_text("# Rules")
        projects_by_root = {}
        MacOSJunieRulesExtractor()._extract_global_rules(projects_by_root)
        self.assertIn(str(self.home), projects_by_root)
        self.assertEqual(len(projects_by_root[str(self.home)]), 1)


class TestMacOSJunieMCPConfigExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("scripts.coding_discovery_tools.macos.junie.mcp_config_extractor.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.mcp_config_extractor.Path.home")
    def test_extracts_mcp_config(self, mock_home, _):
        mock_home.return_value = self.home
        _write_mcp_json(
            self.home / ".junie" / "mcp" / "mcp.json",
            {"my-server": {"command": "npx", "args": ["-y", "my-server"]}},
        )
        result = MacOSJunieMCPConfigExtractor().extract_mcp_config()
        self.assertIsNotNone(result)
        self.assertEqual(len(result["projects"]), 1)


class TestWindowsJunieDetector(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detect_via_scan_finds_user(self):
        (self.home / ".junie").mkdir()

        def fake_scan(cb):
            cb(self.home)

        with patch("scripts.coding_discovery_tools.windows.junie.junie.scan_windows_user_directories", side_effect=fake_scan):
            result = WindowsJunieDetector().detect()

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")


class TestWindowsJunieRulesExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_global_rules_extracted(self):
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "global.md").write_text("# Global Rule")

        projects_by_root = {}
        with patch(
            "scripts.coding_discovery_tools.windows.junie.junie_rules_extractor.scan_windows_user_directories",
            side_effect=lambda cb: cb(self.home),
        ):
            WindowsJunieRulesExtractor()._extract_global_rules(projects_by_root)

        self.assertIn(str(self.home), projects_by_root)


class TestWindowsJunieMCPConfigExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_extracts_mcp_config(self):
        _write_mcp_json(
            self.home / ".junie" / "mcp" / "mcp.json",
            {"tool": {"command": "uvx", "args": ["tool"]}},
        )

        with patch(
            "scripts.coding_discovery_tools.windows.junie.mcp_config_extractor.scan_windows_user_directories",
            side_effect=lambda cb: cb(self.home),
        ):
            result = WindowsJunieMCPConfigExtractor().extract_mcp_config()

        self.assertIsNotNone(result)
        self.assertEqual(len(result["projects"]), 1)


if __name__ == "__main__":
    unittest.main()
