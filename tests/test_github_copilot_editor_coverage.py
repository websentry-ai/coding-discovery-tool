"""Copilot must be detected in Cursor, not only stock VS Code.

The marketplace scan read ``~/.vscode/extensions`` alone, so a user running
Copilot inside Cursor reported no tool at all. Scope matches what the rules and
MCP extractors can enrich, so the other forks stay out until they can be too.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.vscode_extension_helpers import vscode_family_editor_dirs
from scripts.coding_discovery_tools.linux.github_copilot.detect_copilot import (
    LinuxCopilotDetector,
)
from scripts.coding_discovery_tools.macos.github_copilot.detect_copilot import (
    MacOSCopilotDetector,
)
from scripts.coding_discovery_tools.windows.github_copilot.detect_copilot import (
    WindowsGitHubCopilotDetector,
)

_EXT_DIR = {
    "Code": ".vscode/extensions",
    "Cursor": ".cursor/extensions",
    "Windsurf": ".windsurf/extensions",
    "VSCodium": ".vscode-oss/extensions",
    "Antigravity": ".antigravity/extensions",
}


class _Fixture(unittest.TestCase):
    DETECTOR = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.user_home = Path(self.tmp) / "alice"
        self.user_home.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install(self, editor: str, ext_id: str, version: str = "1.2.3") -> Path:
        registry = self.user_home / _EXT_DIR[editor] / "extensions.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps([{"identifier": {"id": ext_id}, "version": version}]), encoding="utf-8"
        )
        return registry.parent

    def _detect(self):
        det = type(self).DETECTOR()
        # Neutralise the built-in fallback so these assert the marketplace path only.
        with patch.object(det, "_detect_vscode_builtin_copilot", return_value=[]):
            return det._detect_vscode_for_user(self.user_home)


class _EditorCoverageCase(_Fixture):
    def test_detected_in_every_supported_editor(self):
        for editor, label in (("Code", "VS Code"), ("Cursor", "Cursor")):
            with self.subTest(editor=editor):
                self.tearDown()
                self.setUp()
                ext_dir = self._install(editor, "github.copilot")
                res = self._detect()
                self.assertEqual(1, len(res))
                self.assertEqual(f"GitHub Copilot ({label})", res[0]["name"])
                self.assertEqual(str(ext_dir), res[0]["install_path"])
                self.assertEqual("1.2.3", res[0]["version"])

    def test_chat_extension_labelled_per_editor(self):
        self._install("Cursor", "github.copilot-chat", "9.9.9")
        res = self._detect()
        self.assertEqual(["GitHub Copilot Chat (Cursor)"], [r["name"] for r in res])
        self.assertEqual("9.9.9", res[0]["version"])

    def test_multiple_editors_each_reported(self):
        self._install("Code", "github.copilot")
        self._install("Cursor", "github.copilot")
        self.assertEqual(
            {"GitHub Copilot (VS Code)", "GitHub Copilot (Cursor)"},
            {r["name"] for r in self._detect()},
        )

    def test_unenrichable_forks_are_not_reported(self):
        """Detection tracks enrichment: a row we cannot enrich would look clean
        rather than unscanned."""
        for editor in ("Windsurf", "VSCodium", "Antigravity"):
            with self.subTest(editor=editor):
                self.tearDown()
                self.setUp()
                self._install(editor, "github.copilot")
                self.assertEqual([], self._detect())

    def test_nothing_installed_reports_nothing(self):
        self.assertEqual([], self._detect())

    def test_uninstalled_extension_not_reported(self):
        """The registry is rewritten on uninstall; the folder can survive."""
        registry = self.user_home / _EXT_DIR["Cursor"] / "extensions.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text("[]", encoding="utf-8")
        (registry.parent / "github.copilot-1.2.3").mkdir()
        self.assertEqual([], self._detect())


class _BuiltinFallbackCase(_Fixture):
    """The built-in fallback is keyed to stock VS Code, so a fork must not
    suppress it."""

    def _detect_with_builtin(self):
        det = type(self).DETECTOR()
        with patch.object(det, "_detect_vscode_builtin_copilot",
                          return_value=[{"name": "GitHub Copilot (VS Code)", "version": "builtin",
                                         "publisher": "GitHub", "install_path": "/builtin"}]):
            return det._detect_vscode_for_user(self.user_home)

    def test_cursor_extension_does_not_suppress_builtin(self):
        self._install("Cursor", "github.copilot")
        names = [r["name"] for r in self._detect_with_builtin()]
        self.assertIn("GitHub Copilot (Cursor)", names)
        self.assertIn("GitHub Copilot (VS Code)", names)

    def test_vscode_extension_still_suppresses_builtin(self):
        self._install("Code", "github.copilot")
        res = self._detect_with_builtin()
        self.assertEqual(["GitHub Copilot (VS Code)"], [r["name"] for r in res])
        self.assertEqual("1.2.3", res[0]["version"])


class TestMacosEditorCoverage(_EditorCoverageCase):
    DETECTOR = MacOSCopilotDetector


class TestWindowsEditorCoverage(_EditorCoverageCase):
    DETECTOR = WindowsGitHubCopilotDetector


class TestLinuxEditorCoverage(_EditorCoverageCase):
    DETECTOR = LinuxCopilotDetector


class TestMacosBuiltinFallback(_BuiltinFallbackCase):
    DETECTOR = MacOSCopilotDetector


class TestWindowsBuiltinFallback(_BuiltinFallbackCase):
    DETECTOR = WindowsGitHubCopilotDetector


class TestLinuxBuiltinFallback(_BuiltinFallbackCase):
    DETECTOR = LinuxCopilotDetector


if __name__ == "__main__":
    unittest.main()


class TestEditorDirRouting(unittest.TestCase):
    """A row's editor decides which user-data dir its rules and MCP come from."""

    def test_maps_each_row_to_its_own_editor(self):
        self.assertEqual(["Code"], vscode_family_editor_dirs("GitHub Copilot (VS Code)"))
        self.assertEqual(["Cursor"], vscode_family_editor_dirs("GitHub Copilot Chat (Cursor)"))

    def test_jetbrains_row_gets_no_vscode_dirs(self):
        self.assertEqual([], vscode_family_editor_dirs("GitHub Copilot PyCharm"))

    def test_unnamed_tool_keeps_the_legacy_union(self):
        self.assertEqual(["Code", "Cursor"], vscode_family_editor_dirs(None))


class TestCursorEnrichmentSources(unittest.TestCase):
    """Cursor rows must read Cursor's own dirs, not VS Code's and not JetBrains'."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "alice"
        self.app_support = self.home / "Library" / "Application Support"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mcp(self, editor: str, servers: dict) -> Path:
        path = self.app_support / editor / "User" / "mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
        return path

    def _extract(self, tool_name: str):
        from scripts.coding_discovery_tools.macos.github_copilot import mcp_config_extractor as mod
        ex = mod.MacOSGitHubCopilotMCPConfigExtractor()
        with patch.object(ex, "_extract_workspace_configs", return_value=[]), \
             patch.object(ex, "_extract_jetbrains_configs", return_value=[{"path": "JETBRAINS"}]), \
             patch(f"{mod.__name__}.extract_ide_global_configs_with_root_support",
                   side_effect=lambda fn, tool_name=None: fn(self.home)):
            return ex.extract_mcp_config(tool_name=tool_name) or {"projects": []}

    def test_cursor_row_reads_cursor_user_dir(self):
        self._mcp("Cursor", {"cursor-server": {}})
        self._mcp("Code", {"vscode-server": {}})
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (Cursor)")["projects"]]
        self.assertTrue(any("Cursor" in p for p in paths), paths)
        self.assertFalse(any(p.endswith("Code/User") for p in paths), paths)

    def test_cursor_row_does_not_read_jetbrains(self):
        self._mcp("Cursor", {"cursor-server": {}})
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (Cursor)")["projects"]]
        self.assertNotIn("JETBRAINS", paths)

    def test_vscode_row_unchanged(self):
        self._mcp("Code", {"vscode-server": {}})
        self._mcp("Cursor", {"cursor-server": {}})
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (VS Code)")["projects"]]
        self.assertTrue(any(p.endswith("Code/User") for p in paths), paths)
        self.assertFalse(any("Cursor" in p for p in paths), paths)


# The bases only carry the cases; running them directly would double-count.
del _Fixture, _EditorCoverageCase, _BuiltinFallbackCase
