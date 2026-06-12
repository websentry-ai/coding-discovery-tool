"""
Unit tests for Junie rules/MCP extraction on Linux.

10 tests covering the critical paths:
- find_junie_project_root path resolution
- LinuxJunieDetector: present/absent .junie dir, version from config
- LinuxJunieRulesExtractor: global rule extraction
- LinuxJunieMCPConfigExtractor: reads ~/.junie/mcp/mcp.json
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.linux.junie.junie import LinuxJunieDetector

# Module path for the central binary/plugin resolver the junie detector calls.
_UTD = "scripts.coding_discovery_tools.user_tool_detector"
_JUNIE_MOD = "scripts.coding_discovery_tools.linux.junie.junie"


def _make_exec(path: Path) -> Path:
    """Create an executable file (a fake junie binary) at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


def _write_jetbrains_plugin(ide_config_dir: Path, plugin_id: str, plugin_name: str) -> None:
    """Write a realistic JetBrains plugin (``plugins/<id>/META-INF/plugin.xml``)
    under an IDE config dir so the detector's real ``_get_plugins`` reads it."""
    meta_inf = ide_config_dir / "plugins" / plugin_id / "META-INF"
    meta_inf.mkdir(parents=True, exist_ok=True)
    (meta_inf / "plugin.xml").write_text(
        f"<idea-plugin><id>{plugin_id}</id><name>{plugin_name}</name></idea-plugin>",
        encoding="utf-8",
    )


def _linux_jetbrains_ide_dir(user_home: Path, ide_folder: str) -> Path:
    """Path to a per-user JetBrains IDE config dir on Linux, created on disk."""
    ide_dir = user_home / ".config" / "JetBrains" / ide_folder
    ide_dir.mkdir(parents=True, exist_ok=True)
    return ide_dir
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

    def _linux_resolver_patches(self):
        """Patches that pin the central binary resolver to the Linux POSIX path
        deterministically (so these run on the macOS/Windows CI runners): force
        ``platform.system='Linux'``, non-root, and a None ``which`` backstop."""
        return (
            patch(f"{_UTD}.platform.system", return_value="Linux"),
            patch(f"{_UTD}.is_running_as_root", return_value=False),
            patch(f"{_UTD}.run_command", return_value=None),
        )

    # --- residue-only: NOT detected (the FP fix / regression guard) -------

    def test_residue_junie_dir_only_not_detected(self):
        """A bare ``~/.junie`` (guidelines residue) is NOT a real install."""
        (self.home / ".junie").mkdir()
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(LinuxJunieDetector, "_has_junie_jetbrains_plugin", return_value=None), \
             p1, p2, p3:
            self.assertIsNone(LinuxJunieDetector().detect())

    def test_returns_none_when_nothing_present(self):
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(LinuxJunieDetector, "_has_junie_jetbrains_plugin", return_value=None), \
             p1, p2, p3:
            self.assertIsNone(LinuxJunieDetector().detect())

    # --- real install signals: detected -----------------------------------

    def test_detects_when_binary_present(self):
        junie_bin = _make_exec(self.home / ".local" / "bin" / "junie")
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home]), p1, p2, p3:
            result = LinuxJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")
        self.assertEqual(result["install_path"], str(junie_bin))

    def test_detects_when_jetbrains_plugin_present(self):
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(LinuxJunieDetector, "_has_junie_jetbrains_plugin", return_value="/cfg/PyCharm"), \
             p1, p2, p3:
            result = LinuxJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/cfg/PyCharm")

    def test_version_read_from_config_json_when_binary_present(self):
        _make_exec(self.home / ".local" / "bin" / "junie")
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "config.json").write_text(json.dumps({"version": "2.1.0"}))
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home]), p1, p2, p3:
            result = LinuxJunieDetector().detect()
        self.assertEqual(result["version"], "2.1.0")

    def test_multi_user_returns_first_found(self):
        home2 = Path(self._tmp) / "home" / "bob"
        home2.mkdir(parents=True)
        # Only alice has a real binary; bob has residue only -> alice is found.
        alice_bin = _make_exec(self.home / ".local" / "bin" / "junie")
        (home2 / ".junie").mkdir()
        p1, p2, p3 = self._linux_resolver_patches()
        with patch(f"{_JUNIE_MOD}.get_linux_user_homes", return_value=[self.home, home2]), \
             patch.object(LinuxJunieDetector, "_has_junie_jetbrains_plugin", return_value=None), \
             p1, p2, p3:
            result = LinuxJunieDetector().detect()
        self.assertEqual(result["install_path"], str(alice_bin))

    def test_jetbrains_plugin_helper_matches_junie(self):
        """The helper matches a real on-disk JetBrains IDE with a 'Junie' plugin
        and returns its per-user config path; a Copilot-only IDE yields None.
        Exercises the real Linux ``.config/JetBrains`` per-user scan +
        ``_get_plugins`` path (no mocked-out helper)."""
        det = LinuxJunieDetector()

        ide_dir = _linux_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        _write_jetbrains_plugin(ide_dir, "intellij-junie", "Junie")
        self.assertEqual(det._has_junie_jetbrains_plugin(self.home), str(ide_dir))

    def test_jetbrains_plugin_helper_no_junie_returns_none(self):
        """A Copilot-only IDE (no Junie plugin) yields None."""
        det = LinuxJunieDetector()
        ide_dir = _linux_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        self.assertIsNone(det._has_junie_jetbrains_plugin(self.home))

    def test_cross_user_scan_not_misattributed(self):
        """REGRESSION (cross-user FP): user alice has the Junie plugin, user bob
        does NOT. ``LinuxJetBrainsDetector.detect()`` iterates *every* user home
        internally, so the old helper attributed alice's plugin to bob. The
        per-user scoped scan reads only bob's ``.config/JetBrains`` and returns
        None. FAILS before the C1 fix, PASSES after."""
        bob = Path(self._tmp) / "home" / "bob"
        bob.mkdir(parents=True)

        alice_ide = _linux_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(alice_ide, "intellij-junie", "Junie")
        bob_ide = _linux_jetbrains_ide_dir(bob, "PyCharm2024.1")
        _write_jetbrains_plugin(bob_ide, "github-copilot", "GitHub Copilot")

        det = LinuxJunieDetector()
        # detect() iterates all user homes — make sure both are visible to the
        # detector so the old (unscoped) code would have found alice's plugin.
        with patch(
            "scripts.coding_discovery_tools.linux.jetbrains.jetbrains.get_linux_user_homes",
            return_value=[self.home, bob],
        ):
            self.assertIsNone(det._has_junie_jetbrains_plugin(bob))
            self.assertEqual(det._has_junie_jetbrains_plugin(self.home), str(alice_ide))


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
