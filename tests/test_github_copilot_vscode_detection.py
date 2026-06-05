"""Tests for VS Code GitHub Copilot detection (macOS), incl. built-in Copilot.

Guards the gap where Copilot shipped built-in with the VS Code app (not in the
per-user ``~/.vscode/extensions/extensions.json``) was never detected, so the
user's VS Code MCP servers (``Code/User/mcp.json``) were silently skipped.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.github_copilot.detect_copilot import (
    MacOSCopilotDetector,
)

_MOD = "scripts.coding_discovery_tools.macos.github_copilot.detect_copilot"


class TestVscodeBuiltinCopilotDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.user_home = Path(self.tmp) / "user"
        self.user_home.mkdir(parents=True)
        # Fake VS Code app bundle with a built-in copilot extension.
        self.app_ext = Path(self.tmp) / "VSCode.app" / "extensions"
        self.copilot = self.app_ext / "copilot"
        self.copilot.mkdir(parents=True)
        (self.copilot / "package.json").write_text(
            json.dumps({"name": "copilot-chat", "publisher": "GitHub", "version": "0.51.0"}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_code_user_dir(self):
        (self.user_home / "Library" / "Application Support" / "Code" / "User").mkdir(parents=True)

    def _make_marketplace_ext(self, ext_id: str, version: str):
        p = self.user_home / ".vscode" / "extensions" / "extensions.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps([{"identifier": {"id": ext_id}, "version": version}]), encoding="utf-8")

    def _detect(self):
        det = MacOSCopilotDetector()
        with patch(f"{_MOD}._VSCODE_APP_EXTENSION_ROOTS", [self.app_ext]):
            return det._detect_vscode_for_user(self.user_home)

    def test_builtin_detected_when_user_uses_vscode(self):
        self._make_code_user_dir()
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot Chat (VS Code)")
        self.assertEqual(res[0]["version"], "0.51.0")
        self.assertEqual(res[0]["install_path"], str(self.copilot))

    def test_builtin_not_attributed_when_user_does_not_use_vscode(self):
        # No Code/User dir -> a machine-wide app install isn't this user's.
        self.assertEqual(self._detect(), [])

    def test_marketplace_extension_takes_precedence(self):
        self._make_code_user_dir()
        self._make_marketplace_ext("github.copilot", "1.250.0")
        res = self._detect()
        # Marketplace wins; no duplicate built-in entry.
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["version"], "1.250.0")
        self.assertNotEqual(res[0]["install_path"], str(self.copilot))

    def test_no_app_builtin_means_no_detection(self):
        # User uses VS Code but the app ships no built-in copilot.
        self._make_code_user_dir()
        shutil.rmtree(self.copilot)
        self.assertEqual(self._detect(), [])

    def test_non_dict_package_json_does_not_crash(self):
        # A package.json that parses to a non-object must not raise (it would
        # otherwise abort all Copilot detection for the run); version -> unknown.
        self._make_code_user_dir()
        (self.copilot / "package.json").write_text("[1, 2, 3]", encoding="utf-8")
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["version"], "unknown")

    def test_builtin_plain_copilot_labeled_generic(self):
        # A bundle whose manifest is a plain "copilot" (not chat) stays generic.
        self._make_code_user_dir()
        (self.copilot / "package.json").write_text(
            json.dumps({"name": "copilot", "publisher": "GitHub", "version": "1.2.3"}), encoding="utf-8")
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot (VS Code)")

    def test_marketplace_present_does_not_invoke_builtin_fallback(self):
        """Purely-additive guarantee: when a marketplace Copilot extension is
        present, the existing path returns it and the new built-in fallback is
        never consulted (existing behavior unchanged)."""
        self._make_code_user_dir()
        self._make_marketplace_ext("github.copilot", "1.250.0")
        with patch.object(MacOSCopilotDetector, "_detect_vscode_builtin_copilot") as spy, \
             patch(f"{_MOD}._VSCODE_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = MacOSCopilotDetector()._detect_vscode_for_user(self.user_home)
        spy.assert_not_called()
        self.assertEqual([r["version"] for r in res], ["1.250.0"])


_LINUX_MOD = "scripts.coding_discovery_tools.linux.github_copilot.detect_copilot"
_WIN_MOD = "scripts.coding_discovery_tools.windows.github_copilot.detect_copilot"


class TestLinuxVscodeBuiltinCopilotDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.linux.github_copilot.detect_copilot import LinuxCopilotDetector
        self.Detector = LinuxCopilotDetector
        self.tmp = tempfile.mkdtemp()
        self.user_home = Path(self.tmp) / "user"
        self.user_home.mkdir(parents=True)
        self.app_ext = Path(self.tmp) / "usr" / "share" / "code" / "resources" / "app" / "extensions"
        self.copilot = self.app_ext / "copilot"
        self.copilot.mkdir(parents=True)
        (self.copilot / "package.json").write_text(json.dumps({"name": "copilot-chat", "version": "0.51.0"}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_code_user_dir(self):
        (self.user_home / ".config" / "Code" / "User").mkdir(parents=True)

    def _detect(self):
        with patch(f"{_LINUX_MOD}._VSCODE_APP_EXTENSION_ROOTS", [self.app_ext]):
            return self.Detector()._detect_vscode_for_user(self.user_home)

    def test_builtin_detected_when_user_uses_vscode(self):
        self._make_code_user_dir()
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot Chat (VS Code)")
        self.assertEqual(res[0]["version"], "0.51.0")
        self.assertEqual(res[0]["install_path"], str(self.copilot))

    def test_builtin_not_attributed_when_user_does_not_use_vscode(self):
        self.assertEqual(self._detect(), [])

    def test_marketplace_present_does_not_invoke_builtin_fallback(self):
        """Existing behavior unchanged: marketplace extension wins, fallback skipped."""
        self._make_code_user_dir()
        p = self.user_home / ".vscode" / "extensions" / "extensions.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps([{"identifier": {"id": "github.copilot"}, "version": "1.250.0"}]), encoding="utf-8")
        with patch.object(self.Detector, "_detect_vscode_builtin_copilot") as spy, \
             patch(f"{_LINUX_MOD}._VSCODE_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = self.Detector()._detect_vscode_for_user(self.user_home)
        spy.assert_not_called()
        self.assertEqual([r["version"] for r in res], ["1.250.0"])

    def test_builtin_plain_copilot_labeled_generic(self):
        self._make_code_user_dir()
        (self.copilot / "package.json").write_text(
            json.dumps({"name": "copilot", "version": "1.2.3"}), encoding="utf-8")
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot (VS Code)")


class TestWindowsVscodeBuiltinCopilotDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.windows.github_copilot.detect_copilot import WindowsGitHubCopilotDetector
        self.Detector = WindowsGitHubCopilotDetector
        self.tmp = tempfile.mkdtemp()
        self.user_home = Path(self.tmp) / "user"
        self.user_home.mkdir(parents=True)
        self.app_ext = Path(self.tmp) / "Program Files" / "Microsoft VS Code" / "resources" / "app" / "extensions"
        self.copilot = self.app_ext / "copilot"
        self.copilot.mkdir(parents=True)
        (self.copilot / "package.json").write_text(json.dumps({"name": "copilot-chat", "version": "0.51.0"}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_code_user_dir(self):
        (self.user_home / "AppData" / "Roaming" / "Code" / "User").mkdir(parents=True)

    def _detect(self):
        # No ~/.vscode/extensions exists -> exercises the fallback path even when
        # the user has no marketplace extensions at all.
        with patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", [self.app_ext]):
            return self.Detector()._detect_vscode_for_user(self.user_home)

    def test_builtin_detected_without_marketplace_dir(self):
        self._make_code_user_dir()
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot Chat (VS Code)")
        self.assertEqual(res[0]["version"], "0.51.0")
        self.assertEqual(res[0]["install_path"], str(self.copilot))

    def test_builtin_not_attributed_when_user_does_not_use_vscode(self):
        self.assertEqual(self._detect(), [])

    def test_marketplace_present_does_not_invoke_builtin_fallback(self):
        """Existing Windows glob path unchanged: a marketplace github.copilot*
        folder is detected and the new built-in fallback is never consulted."""
        self._make_code_user_dir()
        mk = self.user_home / ".vscode" / "extensions" / "github.copilot-1.250.0"
        mk.mkdir(parents=True)
        (mk / "package.json").write_text(json.dumps({"version": "1.250.0"}), encoding="utf-8")
        with patch.object(self.Detector, "_detect_vscode_builtin_copilot") as spy, \
             patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = self.Detector()._detect_vscode_for_user(self.user_home)
        spy.assert_not_called()
        self.assertEqual([r["version"] for r in res], ["1.250.0"])

    def test_builtin_plain_copilot_labeled_generic(self):
        self._make_code_user_dir()
        (self.copilot / "package.json").write_text(
            json.dumps({"name": "copilot", "version": "1.2.3"}), encoding="utf-8")
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot (VS Code)")


if __name__ == "__main__":
    unittest.main()
