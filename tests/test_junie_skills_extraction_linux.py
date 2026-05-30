"""
Unit tests for Junie rules/MCP extraction on Linux.

10 tests covering the critical paths:
- find_junie_project_root path resolution
- LinuxJunieDetector: present/absent .junie dir, version from config
- LinuxJunieRulesExtractor: global rule extraction
- LinuxJunieMCPConfigExtractor: reads ~/.junie/mcp/mcp.json
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.linux.junie.junie import LinuxJunieDetector
from scripts.coding_discovery_tools.linux.junie.junie_rules_extractor import (
    LinuxJunieRulesExtractor,
    find_junie_project_root,
)
from scripts.coding_discovery_tools.linux.junie.mcp_config_extractor import LinuxJunieMCPConfigExtractor


def _write_mcp_json(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestFindJunieProjectRootLinux(unittest.TestCase):

    def test_project_level_rule_returns_project(self):
        rule = Path("/home/alice/myproject/.junie/guidelines.md")
        self.assertEqual(find_junie_project_root(rule), Path("/home/alice/myproject"))

    def test_user_level_rule_returns_home(self):
        rule = Path("/home/alice/.junie/global.md")
        self.assertEqual(find_junie_project_root(rule), Path("/home/alice"))


class TestLinuxJunieDetector(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home" / "alice"
        self.home.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detects_when_junie_dir_exists(self):
        (self.home / ".junie").mkdir()
        with patch("scripts.coding_discovery_tools.linux.junie.junie.get_linux_user_homes", return_value=[self.home]):
            result = LinuxJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")

    def test_returns_none_when_no_junie_dir(self):
        with patch("scripts.coding_discovery_tools.linux.junie.junie.get_linux_user_homes", return_value=[self.home]):
            self.assertIsNone(LinuxJunieDetector().detect())

    def test_version_read_from_config_json(self):
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "config.json").write_text(json.dumps({"version": "2.1.0"}))
        with patch("scripts.coding_discovery_tools.linux.junie.junie.get_linux_user_homes", return_value=[self.home]):
            result = LinuxJunieDetector().detect()
        self.assertEqual(result["version"], "2.1.0")

    def test_multi_user_returns_first_found(self):
        home2 = Path(self._tmp) / "home" / "bob"
        home2.mkdir(parents=True)
        (self.home / ".junie").mkdir()
        (home2 / ".junie").mkdir()
        with patch("scripts.coding_discovery_tools.linux.junie.junie.get_linux_user_homes", return_value=[self.home, home2]):
            result = LinuxJunieDetector().detect()
        self.assertEqual(result["install_path"], str(self.home / ".junie"))


class TestLinuxJunieRulesExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home" / "alice"
        self.home.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_global_rule_extracted(self):
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "guidelines.md").write_text("# Rules")
        projects_by_root = {}
        with patch("scripts.coding_discovery_tools.linux.junie.junie_rules_extractor.get_linux_user_homes", return_value=[self.home]):
            LinuxJunieRulesExtractor()._extract_global_rules(projects_by_root)
        self.assertIn(str(self.home), projects_by_root)
        self.assertEqual(len(projects_by_root[str(self.home)]), 1)


class TestLinuxJunieMCPConfigExtractor(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home" / "alice"
        self.home.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_extracts_mcp_config(self):
        _write_mcp_json(
            self.home / ".junie" / "mcp" / "mcp.json",
            {"my-server": {"command": "npx", "args": ["-y", "my-server"]}},
        )
        with patch("scripts.coding_discovery_tools.linux.junie.mcp_config_extractor.get_linux_user_homes", return_value=[self.home]):
            result = LinuxJunieMCPConfigExtractor().extract_mcp_config()
        self.assertIsNotNone(result)
        self.assertEqual(len(result["projects"]), 1)

    def test_returns_none_when_no_mcp_json(self):
        with patch("scripts.coding_discovery_tools.linux.junie.mcp_config_extractor.get_linux_user_homes", return_value=[self.home]):
            result = LinuxJunieMCPConfigExtractor().extract_mcp_config()
        self.assertIsNone(result)

    def test_multi_user_collects_all_configs(self):
        home2 = Path(self._tmp) / "home" / "bob"
        home2.mkdir(parents=True)
        for home in [self.home, home2]:
            _write_mcp_json(
                home / ".junie" / "mcp" / "mcp.json",
                {"srv": {"command": "npx", "args": []}},
            )
        with patch("scripts.coding_discovery_tools.linux.junie.mcp_config_extractor.get_linux_user_homes", return_value=[self.home, home2]):
            result = LinuxJunieMCPConfigExtractor().extract_mcp_config()
        self.assertEqual(len(result["projects"]), 2)


if __name__ == "__main__":
    unittest.main()
