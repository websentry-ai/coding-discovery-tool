"""
Unit tests for Junie rules/MCP extraction (macOS and Windows).

10 tests covering the critical paths:
- find_junie_project_root path resolution
- Detector: present/absent .junie dir, version from config
- RulesExtractor: global rule extraction
- MCPConfigExtractor: reads ~/.junie/mcp/mcp.json
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


# Module path for the central binary/plugin resolver the junie detectors call.
_UTD = "scripts.coding_discovery_tools.user_tool_detector"


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


def _macos_jetbrains_ide_dir(user_home: Path, ide_folder: str) -> Path:
    """Path to a per-user JetBrains IDE config dir on macOS, created on disk."""
    ide_dir = user_home / "Library" / "Application Support" / "JetBrains" / ide_folder
    ide_dir.mkdir(parents=True, exist_ok=True)
    return ide_dir


def _windows_jetbrains_ide_dir(user_home: Path, ide_folder: str) -> Path:
    """Path to a per-user JetBrains IDE config dir on Windows, created on disk."""
    ide_dir = user_home / "AppData" / "Roaming" / "JetBrains" / ide_folder
    ide_dir.mkdir(parents=True, exist_ok=True)
    return ide_dir


class TestMacOSJunieDetector(unittest.TestCase):
    """Detection now gates on the junie binary OR a Junie JetBrains plugin —
    NOT on the ``~/.junie`` guidelines dir, which is residue that survives
    uninstall. ``~/.junie`` stays the version source only."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- residue-only: NOT detected (the FP fix / regression guard) -------

    @patch(f"{_UTD}.run_command", return_value=None)
    @patch(f"{_UTD}.platform.system", return_value="Darwin")
    @patch(f"{_UTD}.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_residue_junie_dir_only_not_detected(self, mock_home, *_):
        mock_home.return_value = self.home
        (self.home / ".junie").mkdir()
        with patch.object(MacOSJunieDetector, "_has_junie_jetbrains_plugin", return_value=None):
            self.assertIsNone(MacOSJunieDetector().detect())

    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_returns_none_when_nothing_present(self, mock_home, _):
        mock_home.return_value = self.home
        with patch.object(MacOSJunieDetector, "_has_junie_jetbrains_plugin", return_value=None):
            self.assertIsNone(MacOSJunieDetector().detect())

    # --- real install signals: detected -----------------------------------

    @patch(f"{_UTD}.run_command", return_value=None)
    @patch(f"{_UTD}.platform.system", return_value="Darwin")
    @patch(f"{_UTD}.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_detects_when_binary_present(self, mock_home, *_):
        mock_home.return_value = self.home
        junie_bin = _make_exec(self.home / ".local" / "bin" / "junie")
        result = MacOSJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")
        self.assertEqual(result["install_path"], str(junie_bin))

    @patch(f"{_UTD}.run_command", return_value=None)
    @patch(f"{_UTD}.platform.system", return_value="Darwin")
    @patch(f"{_UTD}.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_detects_when_jetbrains_plugin_present(self, mock_home, *_):
        mock_home.return_value = self.home
        plugin_path = "/Users/test/Library/Application Support/JetBrains/PyCharm2024.1"
        with patch.object(MacOSJunieDetector, "_has_junie_jetbrains_plugin", return_value=plugin_path):
            result = MacOSJunieDetector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], plugin_path)

    @patch(f"{_UTD}.run_command", return_value=None)
    @patch(f"{_UTD}.platform.system", return_value="Darwin")
    @patch(f"{_UTD}.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.junie.junie.Path.home")
    def test_version_read_from_config_json_when_binary_present(self, mock_home, *_):
        mock_home.return_value = self.home
        _make_exec(self.home / ".local" / "bin" / "junie")
        junie_dir = self.home / ".junie"
        junie_dir.mkdir()
        (junie_dir / "config.json").write_text(json.dumps({"version": "1.2.3"}))
        self.assertEqual(MacOSJunieDetector().detect()["version"], "1.2.3")

    def test_jetbrains_plugin_helper_matches_junie(self):
        """The plugin helper matches a real on-disk JetBrains IDE that has a
        'Junie' plugin and returns its config path; a Copilot-only IDE yields
        None. Exercises the real per-user ``_scan_jetbrains_config_dir`` +
        ``_get_plugins`` path (no mocked-out helper) so the ``config_path`` key
        wiring is covered."""
        det = MacOSJunieDetector()

        ide_dir = _macos_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        # Junie-only present: matched, returns the scoped config path.
        _write_jetbrains_plugin(ide_dir, "intellij-junie", "Junie")
        self.assertEqual(det._has_junie_jetbrains_plugin(self.home), str(ide_dir))

    def test_jetbrains_plugin_helper_no_junie_returns_none(self):
        """A Copilot-only IDE (no Junie plugin) yields None."""
        det = MacOSJunieDetector()
        ide_dir = _macos_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        self.assertIsNone(det._has_junie_jetbrains_plugin(self.home))

    @patch("scripts.coding_discovery_tools.macos.jetbrains.jetbrains.is_running_as_root", return_value=False)
    @patch("scripts.coding_discovery_tools.macos.jetbrains.jetbrains.Path.home")
    def test_cross_user_scan_not_misattributed(self, mock_home, _mock_root):
        """REGRESSION (cross-user FP): user A has the Junie JetBrains plugin and
        user B does NOT. Asking the helper about user B must return None — B must
        never inherit A's plugin.

        The bug mechanism: ``MacOSJetBrainsDetector.detect()`` ignores the
        ``user_home`` the junie helper sets and scans ``Path.home()`` (here = user
        A). The old ``detect()``-based helper therefore returned user A's Junie
        config path even when asked about user B. The per-user scoped scan reads
        only user B's config dir, so it correctly returns None.

        FAILS before the C1 fix, PASSES after.
        """
        users = Path(self._tmp) / "Users"
        user_a = users / "userA_has_junie"
        user_b = users / "userB_no_junie"
        user_a.mkdir(parents=True)
        user_b.mkdir(parents=True)
        # detect() (old code) scans Path.home() — pin it to user A.
        mock_home.return_value = user_a

        # user A has a JetBrains IDE with the Junie plugin.
        ide_a = _macos_jetbrains_ide_dir(user_a, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_a, "intellij-junie", "Junie")
        # user B has a JetBrains IDE too, but only Copilot — no Junie.
        ide_b = _macos_jetbrains_ide_dir(user_b, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_b, "github-copilot", "GitHub Copilot")

        det = MacOSJunieDetector()
        # user B must NOT be credited with user A's Junie plugin.
        self.assertIsNone(det._has_junie_jetbrains_plugin(user_b))
        # user A is still correctly detected (no regression of the happy path).
        self.assertEqual(det._has_junie_jetbrains_plugin(user_a), str(ide_a))


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

    def test_residue_junie_dir_only_not_detected(self):
        """A bare ``%USERPROFILE%\\.junie`` (guidelines residue) is NOT a real
        install -> not detected (the FP fix / regression guard)."""
        (self.home / ".junie").mkdir()

        def fake_scan(cb):
            cb(self.home)

        with patch("scripts.coding_discovery_tools.windows.junie.junie.scan_windows_user_directories", side_effect=fake_scan), \
             patch(f"{_UTD}.platform.system", return_value="Windows"), \
             patch.object(WindowsJunieDetector, "_has_junie_jetbrains_plugin", return_value=None):
            result = WindowsJunieDetector().detect()

        self.assertIsNone(result)

    def test_detect_via_scan_finds_user_with_binary(self):
        """A real junie binary (``~/.local/bin/junie.exe``) -> detected."""
        junie_bin = self.home / ".local" / "bin" / "junie.exe"
        junie_bin.parent.mkdir(parents=True, exist_ok=True)
        junie_bin.write_text("")

        def fake_scan(cb):
            cb(self.home)

        with patch("scripts.coding_discovery_tools.windows.junie.junie.scan_windows_user_directories", side_effect=fake_scan), \
             patch(f"{_UTD}.platform.system", return_value="Windows"):
            result = WindowsJunieDetector().detect()

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")
        self.assertEqual(result["install_path"], str(junie_bin))

    def test_detect_via_scan_finds_user_with_plugin(self):
        """A Junie JetBrains plugin (no binary) -> detected."""
        def fake_scan(cb):
            cb(self.home)

        with patch("scripts.coding_discovery_tools.windows.junie.junie.scan_windows_user_directories", side_effect=fake_scan), \
             patch(f"{_UTD}.platform.system", return_value="Windows"), \
             patch.object(WindowsJunieDetector, "_has_junie_jetbrains_plugin", return_value="C:\\cfg\\PyCharm"):
            result = WindowsJunieDetector().detect()

        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "C:\\cfg\\PyCharm")

    def test_jetbrains_plugin_helper_matches_junie(self):
        """The helper matches a real on-disk JetBrains IDE with a 'Junie' plugin
        and returns its per-user config path (via the ``_config_path`` key); a
        Copilot-only IDE yields None. Exercises the real Windows
        ``AppData/Roaming/JetBrains`` user-scoped ``detect()`` + ``_get_plugins``
        path (no mocked-out helper)."""
        det = WindowsJunieDetector()

        ide_dir = _windows_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        _write_jetbrains_plugin(ide_dir, "intellij-junie", "Junie")
        self.assertEqual(det._has_junie_jetbrains_plugin(self.home), str(ide_dir))

    def test_jetbrains_plugin_helper_no_junie_returns_none(self):
        """A Copilot-only IDE (no Junie plugin) yields None."""
        det = WindowsJunieDetector()
        ide_dir = _windows_jetbrains_ide_dir(self.home, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_dir, "github-copilot", "GitHub Copilot")
        self.assertIsNone(det._has_junie_jetbrains_plugin(self.home))

    def test_cross_user_scan_not_misattributed(self):
        """REGRESSION (cross-user FP): user A has the Junie plugin, user B does
        NOT. The Windows JetBrains detector is user_home-scoped, and the junie
        helper additionally guards that the matched config path is under
        ``user_home``. Asking about user B returns None; user A still matches."""
        user_a = Path(self._tmp) / "Users" / "userA_has_junie"
        user_b = Path(self._tmp) / "Users" / "userB_no_junie"
        user_a.mkdir(parents=True)
        user_b.mkdir(parents=True)

        ide_a = _windows_jetbrains_ide_dir(user_a, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_a, "intellij-junie", "Junie")
        ide_b = _windows_jetbrains_ide_dir(user_b, "PyCharm2024.1")
        _write_jetbrains_plugin(ide_b, "github-copilot", "GitHub Copilot")

        det = WindowsJunieDetector()
        self.assertIsNone(det._has_junie_jetbrains_plugin(user_b))
        self.assertEqual(det._has_junie_jetbrains_plugin(user_a), str(ide_a))


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
