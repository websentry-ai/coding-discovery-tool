"""Tests for VS Code GitHub Copilot detection (macOS), incl. built-in Copilot.

Guards the gap where Copilot shipped built-in with the VS Code app (not in the
per-user ``~/.vscode/extensions/extensions.json``) was never detected, so the
user's VS Code MCP servers (``Code/User/mcp.json``) were silently skipped.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.macos.github_copilot.detect_copilot as detect_copilot_mod
from scripts.coding_discovery_tools.macos.github_copilot.detect_copilot import (
    MacOSCopilotDetector,
)
from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import (
    MacOSJetBrainsDetector,
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
        """Windows now reads the LIVE extensions.json registry (matching
        macOS/Linux): a registered github.copilot entry is detected and the
        built-in fallback is never consulted."""
        self._make_code_user_dir()
        reg = self.user_home / ".vscode" / "extensions" / "extensions.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(
            json.dumps([{"identifier": {"id": "github.copilot"}, "version": "1.250.0"}]),
            encoding="utf-8",
        )
        with patch.object(self.Detector, "_detect_vscode_builtin_copilot") as spy, \
             patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = self.Detector()._detect_vscode_for_user(self.user_home)
        spy.assert_not_called()
        self.assertEqual([r["version"] for r in res], ["1.250.0"])

    def test_residue_extension_folder_only_not_detected(self):
        """REGRESSION GUARD (FIX 2): an uninstalled Copilot whose extension
        FOLDER survives (microsoft/vscode#81046) but is absent from the live
        extensions.json registry must NOT be detected. The old folder glob
        produced a phantom row here; the registry read does not. The built-in
        fallback is still consulted (and finds nothing, since no Code/User dir)."""
        # Surviving extension folder, but extensions.json lists nothing.
        ext_dir = self.user_home / ".vscode" / "extensions"
        residue = ext_dir / "github.copilot-1.250.0"
        residue.mkdir(parents=True)
        (residue / "package.json").write_text(json.dumps({"version": "1.250.0"}), encoding="utf-8")
        (ext_dir / "extensions.json").write_text(json.dumps([]), encoding="utf-8")
        with patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = self.Detector()._detect_vscode_for_user(self.user_home)
        self.assertEqual(res, [])

    def test_live_registry_entry_detected(self):
        """A live github.copilot-chat registry entry -> detected with the
        registered version and the extensions dir as install_path."""
        ext_dir = self.user_home / ".vscode" / "extensions"
        ext_dir.mkdir(parents=True)
        (ext_dir / "extensions.json").write_text(
            json.dumps([{"identifier": {"id": "github.copilot-chat"}, "version": "0.30.0"}]),
            encoding="utf-8",
        )
        with patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", [self.app_ext]):
            res = self.Detector()._detect_vscode_for_user(self.user_home)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot Chat (VS Code)")
        self.assertEqual(res[0]["version"], "0.30.0")
        self.assertEqual(res[0]["install_path"], str(ext_dir))

    def test_builtin_plain_copilot_labeled_generic(self):
        self._make_code_user_dir()
        (self.copilot / "package.json").write_text(
            json.dumps({"name": "copilot", "version": "1.2.3"}), encoding="utf-8")
        res = self._detect()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot (VS Code)")

    def test_builtin_detected_in_versioned_user_install(self):
        self._make_code_user_dir()
        copilot = (
            self.user_home
            / "AppData"
            / "Local"
            / "Programs"
            / "Microsoft VS Code"
            / "520fb30b2d"
            / "resources"
            / "app"
            / "extensions"
            / "copilot"
        )
        copilot.mkdir(parents=True)
        (copilot / "package.json").write_text(
            json.dumps({"name": "copilot-chat", "version": "0.64.0"}),
            encoding="utf-8",
        )

        with patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", []):
            res = self.Detector()._detect_vscode_for_user(self.user_home)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "GitHub Copilot Chat (VS Code)")
        self.assertEqual(res[0]["version"], "0.64.0")
        self.assertEqual(res[0]["install_path"], str(copilot))

    def test_versioned_user_install_symlink_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        self._make_code_user_dir()
        external = Path(self.tmp) / "external-install"
        copilot = external / "resources" / "app" / "extensions" / "copilot"
        copilot.mkdir(parents=True)
        (copilot / "package.json").write_text(
            json.dumps({"name": "copilot-chat", "version": "0.64.0"}),
            encoding="utf-8",
        )
        install_root = (
            self.user_home
            / "AppData"
            / "Local"
            / "Programs"
            / "Microsoft VS Code"
        )
        install_root.mkdir(parents=True)
        (install_root / "redirect").symlink_to(external, target_is_directory=True)

        with patch(f"{_WIN_MOD}._VSCODE_SYSTEM_APP_EXTENSION_ROOTS", []):
            result = self.Detector()._detect_vscode_for_user(self.user_home)

        self.assertEqual(result, [])

    def test_builtin_copilot_reparse_point_is_ignored(self):
        self._make_code_user_dir()

        with patch(
            f"{_WIN_MOD}.is_symlink_or_junction",
            side_effect=lambda path: path == self.copilot,
        ):
            result = self._detect()

        self.assertEqual(result, [])

    def test_builtin_package_reparse_point_is_not_read(self):
        self._make_code_user_dir()
        package_json = self.copilot / "package.json"

        with patch(
            f"{_WIN_MOD}.is_symlink_or_junction",
            side_effect=lambda path: path == package_json,
        ):
            result = self._detect()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["version"], "unknown")


class TestMacOSScopedToUserHome(unittest.TestCase):
    """A scoped scan must read only ``user_home``, and must not turn a read
    failure into an absent tool (which would make the install prunable)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.alice = self._make_home("alice")
        self.bob = self._make_home("bob")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_home(self, name):
        return Path(self.tmp) / name

    def _give_vscode_copilot(self, home):
        p = home / ".vscode" / "extensions" / "extensions.json"
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps([{"identifier": {"id": "github.copilot-chat"}, "version": "0.40.1"}]),
            encoding="utf-8",
        )

    def _give_jetbrains_ide(self, home, folder):
        (home / "Library" / "Application Support" / "JetBrains" / folder / "plugins").mkdir(parents=True)

    def _vscode_scan(self, home):
        det = MacOSCopilotDetector()
        det.user_home = home
        return det._detect_vscode_all_users()

    def test_vscode_copilot_only_reported_for_its_owner(self):
        self._give_vscode_copilot(self.alice)
        self.bob.mkdir(parents=True)

        self.assertEqual(len(self._vscode_scan(self.alice)), 1)
        self.assertEqual(self._vscode_scan(self.bob), [])

    def test_jetbrains_scan_reads_only_the_scoped_home(self):
        self._give_jetbrains_ide(self.alice, "IntelliJIdea2025.2")
        self._give_jetbrains_ide(self.bob, "PyCharm2024.1")

        det = MacOSJetBrainsDetector()
        det.user_home = self.alice

        self.assertEqual(
            [ide["folder_name"] for ide in det._scan_for_ides()], ["IntelliJIdea2025.2"]
        )

    def test_unreadable_scoped_home_raises_instead_of_reporting_absent(self):
        det = MacOSJetBrainsDetector()
        det.user_home = self.alice

        with patch.object(
            MacOSJetBrainsDetector,
            "_scan_jetbrains_config_dir",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            with self.assertRaises(PermissionError):
                det._scan_for_ides()

    def test_unreadable_user_data_dir_raises_instead_of_reporting_absent(self):
        det = MacOSCopilotDetector()
        det.user_home = self.alice

        with patch.object(Path, "exists", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(PermissionError):
                det._detect_vscode_builtin_copilot(self.alice)


class TestVscodeInsidersCoverage(unittest.TestCase):
    """Insiders is a VS Code channel, not its own row, so its marketplace Copilot
    must be found and reported under the existing ``(VS Code)`` label."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_registry(self, ext_root: str):
        p = self.home / ext_root / "extensions.json"
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps([{"identifier": {"id": "github.copilot-chat"}, "version": "0.64.1"}]),
            encoding="utf-8",
        )

    def test_insiders_only_copilot_is_detected_as_vs_code(self):
        (self.home / "Library" / "Application Support" / "Code - Insiders" / "User").mkdir(parents=True)
        self._make_registry(".vscode-insiders/extensions")

        det = MacOSCopilotDetector()
        det.user_home = self.home
        results = det._detect_vscode_for_user(self.home)

        self.assertEqual(["GitHub Copilot Chat (VS Code)"], [r["name"] for r in results])
        self.assertTrue(results[0]["install_path"].endswith(".vscode-insiders/extensions"))

    def test_stable_wins_when_both_channels_have_copilot(self):
        self._make_registry(".vscode/extensions")
        self._make_registry(".vscode-insiders/extensions")

        det = MacOSCopilotDetector()
        det.user_home = self.home
        results = det._detect_vscode_for_user(self.home)

        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["install_path"].endswith(".vscode/extensions"))

    def test_short_app_bundle_name_is_probed(self):
        """Some installs keep ``Code.app``; Cline and Roo Code already accept both."""
        roots = [str(p) for p in detect_copilot_mod._VSCODE_APP_EXTENSION_ROOTS]
        self.assertIn("/Applications/Code.app/Contents/Resources/app/extensions", roots)
        self.assertIn(
            "/Applications/Visual Studio Code.app/Contents/Resources/app/extensions", roots
        )


if __name__ == "__main__":
    unittest.main()
