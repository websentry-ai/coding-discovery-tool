"""Copilot must be detected in every VS Code fork, not only stock VS Code.

The marketplace scan read ``~/.vscode/extensions`` alone, so a user running
Copilot inside a fork reported no tool at all. Detection and enrichment share one
editor map, so a detected row always has a user-data dir to read.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    record_vscode_bundle_probe,
    vscode_bundles_probed,
)
from scripts.coding_discovery_tools.vscode_extension_helpers import (
    VSCODE_EDITOR_DISPLAY_NAMES,
    find_extension_in_editor,
    reset_vscode_registry_state,
    vscode_family_editor_dirs,
    vscode_registry_state,
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


_MAC_DETECT = "scripts.coding_discovery_tools.macos.github_copilot.detect_copilot"
_LINUX_DETECT = "scripts.coding_discovery_tools.linux.github_copilot.detect_copilot"

# Where each platform's VS Code keeps its per-user data, relative to the home.
_USER_DATA_REL = {
    "Darwin": Path("Library") / "Application Support",
    "Windows": Path("AppData") / "Roaming",
    "Linux": Path(".config"),
}


class _EvidenceTierMixin:
    """Copilot Chat's own transcripts, the last resort once both the marketplace
    registry and the app bundle come back empty. The editor's config dir is not
    evidence of anything — our installer creates one on every managed device.

    A mixin, not a ``_Fixture`` subclass: pytest collects every TestCase it finds,
    and an abstract case with no DETECTOR would run and fail on its own."""

    OS_NAME = None

    def _no_bundles(self, det):
        """Neutralise the app-bundle probe: a dev box with a real VS Code install
        would otherwise answer from the bundle and never reach the evidence tier."""
        raise NotImplementedError

    def _user_dir(self):
        return self.user_home / _USER_DATA_REL[self.OS_NAME] / "Code" / "User"

    def _write_transcript(self, age_days=0):
        transcripts = self._user_dir() / "workspaceStorage" / "ws1" / "GitHub.copilot-chat" / "transcripts"
        transcripts.mkdir(parents=True, exist_ok=True)
        path = transcripts / "s1.jsonl"
        path.write_text("{}", encoding="utf-8")
        if age_days:
            stale = time.time() - age_days * 86400
            os.utime(path, (stale, stale))
        return path

    def _detect_builtin(self):
        det = type(self).DETECTOR()
        with self._no_bundles(det), \
                patch(f"{utils_mod.__name__}.platform.system", return_value=self.OS_NAME):
            return det._detect_vscode_builtin_copilot(self.user_home)

    def test_recent_transcript_reported(self):
        self._write_transcript()
        res = self._detect_builtin()
        self.assertEqual(["GitHub Copilot Chat (VS Code)"], [r["name"] for r in res])
        self.assertEqual("unknown", res[0]["version"])
        # The editor's User dir, never the workspace dir: install_path is part of the
        # manifest identity, so a per-workspace path would churn the row every scan.
        self.assertEqual(self._user_dir(), Path(res[0]["install_path"]))

    def test_stale_transcript_not_reported(self):
        self._write_transcript(age_days=utils_mod.COPILOT_EVIDENCE_MAX_AGE_DAYS + 5)
        self.assertEqual([], self._detect_builtin())

    def test_user_data_dir_alone_is_not_evidence(self):
        self._user_dir().mkdir(parents=True)
        self.assertEqual([], self._detect_builtin())

    def test_redirected_workspace_is_not_evidence(self):
        """A workspace pointing outside the home would hand another user's Copilot to
        this one under a privileged all-users scan."""
        theirs = Path(self.tmp) / "bob" / "ws" / "GitHub.copilot-chat" / "transcripts"
        theirs.mkdir(parents=True)
        (theirs / "s1.jsonl").write_text("{}", encoding="utf-8")
        workspaces = self._user_dir() / "workspaceStorage"
        workspaces.mkdir(parents=True)
        try:
            (workspaces / "ws1").symlink_to(theirs.parents[1], target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted here")
        self.assertEqual([], self._detect_builtin())

    def test_redirected_ancestor_is_not_evidence(self):
        """A guard on the leaf alone sees nothing: ``lstat`` resolves ancestors, so a
        link at ``Code`` (or ``Library`` / ``AppData`` / ``.config``) hands another
        profile's tree to the probe under this user's own lexical path."""
        theirs = Path(self.tmp) / "bob" / "Code"
        (theirs / "User" / "workspaceStorage" / "ws1"
         / "GitHub.copilot-chat" / "transcripts").mkdir(parents=True)
        (theirs / "User" / "workspaceStorage" / "ws1" / "GitHub.copilot-chat"
         / "transcripts" / "s1.jsonl").write_text("{}", encoding="utf-8")
        mine = self._user_dir().parent          # <home>/<data base>/Code
        mine.parent.mkdir(parents=True, exist_ok=True)
        try:
            mine.symlink_to(theirs, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted here")
        self.assertEqual([], self._detect_builtin())

    def test_recent_workspace_found_past_the_cap(self):
        """Directory order is arbitrary, so the newest workspace has to be read first:
        the one recent transcript must not fall outside the cap by luck and leave a
        live install reported absent."""
        workspaces = self._user_dir() / "workspaceStorage"
        workspaces.mkdir(parents=True)
        stale = time.time() - 400 * 86400
        for index in range(utils_mod._EVIDENCE_DIR_CAP + 20):
            decoy = workspaces / f"decoy{index:04d}"
            decoy.mkdir()
            os.utime(decoy, (stale, stale))
        self._write_transcript()
        self.assertEqual(["GitHub Copilot Chat (VS Code)"],
                         [r["name"] for r in self._detect_builtin()])


class TestMacosEvidenceTier(_EvidenceTierMixin, _Fixture):
    DETECTOR = MacOSCopilotDetector
    OS_NAME = "Darwin"

    def _no_bundles(self, det):
        return patch(f"{_MAC_DETECT}._app_extension_roots", return_value=[])


class TestWindowsEvidenceTier(_EvidenceTierMixin, _Fixture):
    DETECTOR = WindowsGitHubCopilotDetector
    OS_NAME = "Windows"

    def _no_bundles(self, det):
        return patch.object(det, "_vscode_app_extension_roots", return_value=[])


class TestLinuxEvidenceTier(_EvidenceTierMixin, _Fixture):
    DETECTOR = LinuxCopilotDetector
    OS_NAME = "Linux"

    def _no_bundles(self, det):
        return patch(f"{_LINUX_DETECT}._VSCODE_APP_EXTENSION_ROOTS", [])


class TestNoToolsDiscriminators(unittest.TestCase):
    """A zero-tool event has to say WHICH lookup came back empty. A registry that is
    absent, one we were denied, and one that simply does not list Copilot are three
    different bugs behind a single None."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp)
        self._clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self._clear()

    def _clear(self):
        reset_vscode_registry_state()
        utils_mod._vscode_bundles_found.clear()

    def _registry(self, payload):
        registry = self.home / ".vscode" / "extensions" / "extensions.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(payload, encoding="utf-8")

    def test_absent_registry(self):
        find_extension_in_editor(self.home, "Code", "github.copilot")
        self.assertEqual(["Code:missing"], vscode_registry_state())

    def test_registry_without_copilot(self):
        self._registry("[]")
        find_extension_in_editor(self.home, "Code", "github.copilot")
        self.assertEqual(["Code:present"], vscode_registry_state())

    def test_registry_listing_copilot(self):
        self._registry(json.dumps([{"identifier": {"id": "GitHub.Copilot"}, "version": "1.0"}]))
        find_extension_in_editor(self.home, "Code", "github.copilot")
        self.assertEqual(["Code:listed"], vscode_registry_state())

    def test_denied_registry_is_not_reported_as_absent(self):
        self._registry("[]")
        with patch("os.stat", side_effect=PermissionError(13, "denied")):
            find_extension_in_editor(self.home, "Code", "github.copilot")
        self.assertEqual(["Code:unreadable"], vscode_registry_state())

    def test_corrupt_registry_is_unreadable(self):
        self._registry("{not json")
        find_extension_in_editor(self.home, "Code", "github.copilot")
        self.assertEqual(["Code:unreadable"], vscode_registry_state())

    def test_bundle_probe_records_only_roots_on_disk(self):
        present = self.home / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "extensions"
        present.mkdir(parents=True)
        record_vscode_bundle_probe(present)
        record_vscode_bundle_probe(
            self.home / "Code.app" / "Contents" / "Resources" / "app" / "extensions")
        self.assertEqual(["Visual Studio Code.app"], vscode_bundles_probed())


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

class TestAllUsersScanIsScopedToTheUser(unittest.TestCase):
    """The scan runs once per user and is told who it is asking about.

    macOS and Linux ignored that and walked every home, so under a root/MDM scan
    each user was handed every other user's Copilot. Windows already scoped.
    """

    DETECTORS = (MacOSCopilotDetector, LinuxCopilotDetector, WindowsGitHubCopilotDetector)

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_vscode_scan_reads_only_the_scoped_home(self):
        for cls in self.DETECTORS:
            with self.subTest(detector=cls.__name__):
                det = cls()
                det.user_home = Path("/Users/alice")
                with patch.object(det, "_detect_vscode_for_user", return_value=[]) as per_user:
                    det._detect_vscode_all_users()
                per_user.assert_called_once_with(Path("/Users/alice"))

    def test_jetbrains_scan_reads_only_the_scoped_home(self):
        for cls in (MacOSCopilotDetector, WindowsGitHubCopilotDetector):
            with self.subTest(detector=cls.__name__):
                det = cls()
                det.user_home = Path("/Users/alice")
                with patch.object(det, "_detect_jetbrains_for_user", return_value=[]) as per_user:
                    det._detect_jetbrains_all_users()
                per_user.assert_called_once_with(Path("/Users/alice"))


# The bases only carry the cases; running them directly would double-count.
del _Fixture, _EditorCoverageCase, _BuiltinFallbackCase
