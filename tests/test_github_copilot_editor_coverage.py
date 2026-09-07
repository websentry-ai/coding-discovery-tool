"""Copilot must be detected in every VS Code fork, not only stock VS Code.

The marketplace scan read ``~/.vscode/extensions`` alone, so a user running
Copilot inside a fork reported no tool at all. Detection and enrichment share one
editor map, so a detected row always has a user-data dir to read.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.vscode_extension_helpers import (
    VSCODE_EDITOR_DISPLAY_NAMES,
    vscode_family_editor_dirs,
)
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
        for editor, label in VSCODE_EDITOR_DISPLAY_NAMES.items():
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

    def test_every_detected_row_is_enrichable(self):
        """Detection tracks enrichment: a row with no user-data dir to read would
        look clean rather than unscanned."""
        for editor in VSCODE_EDITOR_DISPLAY_NAMES:
            with self.subTest(editor=editor):
                self.tearDown()
                self.setUp()
                self._install(editor, "github.copilot")
                for row in self._detect():
                    self.assertEqual([editor], vscode_family_editor_dirs(row["name"]))

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
        for editor, label in VSCODE_EDITOR_DISPLAY_NAMES.items():
            with self.subTest(editor=editor):
                self.assertEqual([editor], vscode_family_editor_dirs(f"GitHub Copilot ({label})"))
        self.assertEqual(["Cursor"], vscode_family_editor_dirs("GitHub Copilot Chat (Cursor)"))

    def test_jetbrains_row_gets_no_vscode_dirs(self):
        self.assertEqual([], vscode_family_editor_dirs("GitHub Copilot PyCharm"))
        self.assertEqual([], vscode_family_editor_dirs("GitHub Copilot (IntelliJ IDEA)"))

    def test_unnamed_tool_keeps_the_legacy_union(self):
        self.assertEqual(list(VSCODE_EDITOR_DISPLAY_NAMES), vscode_family_editor_dirs(None))


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
        code_user = str(self.app_support / "Code" / "User")
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (Cursor)")["projects"]]
        self.assertTrue(any(str(self.app_support / "Cursor" / "User") == p for p in paths), paths)
        self.assertNotIn(code_user, paths)

    def test_cursor_row_does_not_read_jetbrains(self):
        self._mcp("Cursor", {"cursor-server": {}})
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (Cursor)")["projects"]]
        self.assertNotIn("JETBRAINS", paths)

    def test_vscode_row_unchanged(self):
        self._mcp("Code", {"vscode-server": {}})
        self._mcp("Cursor", {"cursor-server": {}})
        paths = [p.get("path", "") for p in self._extract("GitHub Copilot (VS Code)")["projects"]]
        self.assertIn(str(self.app_support / "Code" / "User"), paths)
        self.assertNotIn(str(self.app_support / "Cursor" / "User"), paths)


class TestPermissionsStayWithVscode(unittest.TestCase):
    """Permissions come from the editor's own settings.json, so a Cursor row must
    not inherit stock VS Code's posture."""

    def _canonical(self, names):
        from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector.__new__(AIToolsDetector)
        det._set_canonical_vscode_copilot([{"name": n} for n in names])
        return det._canonical_vscode_copilot

    def test_vscode_preferred_when_both_present(self):
        self.assertEqual(
            "github copilot chat (vs code)",
            self._canonical(["GitHub Copilot Chat (VS Code)", "GitHub Copilot (Cursor)"]),
        )

    def test_cursor_only_user_still_gets_a_canonical_row(self):
        self.assertEqual("github copilot (cursor)", self._canonical(["GitHub Copilot (Cursor)"]))

    def test_cursor_canonical_row_is_not_a_vscode_row(self):
        """The permissions branch additionally requires a "(vs code)" suffix."""
        canonical = self._canonical(["GitHub Copilot (Cursor)"])
        self.assertFalse(canonical.endswith("(vs code)"))

# The bases only carry the cases; running them directly would double-count.
del _Fixture, _EditorCoverageCase, _BuiltinFallbackCase
