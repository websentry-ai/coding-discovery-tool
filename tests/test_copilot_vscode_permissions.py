"""Tests for the VS Code GitHub Copilot settings/permission extractor.

Covers the permission mapping (settings.json keys → backend-ready record) and the
real per-OS extractor over planted settings.json fixtures.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.coding_tool_base import BaseGitHubCopilotSettingsExtractor  # noqa: E402
from coding_discovery_tools.coding_tool_factory import GitHubCopilotSettingsExtractorFactory  # noqa: E402


class _MapExtractor(BaseGitHubCopilotSettingsExtractor):
    """Concrete stub for exercising the mapping without touching the real FS."""

    def _scan_users(self, callback):
        pass

    def _user_settings_candidates(self, user_home):
        return []

    def _iter_workspace_settings_files(self, user_home):
        return []


class TestPermissionMapping(unittest.TestCase):
    def setUp(self):
        self.ex = _MapExtractor()

    def _rec(self, data):
        return self.ex._build_record(data, Path("/x/settings.json"), "user")

    def test_no_security_keys_yields_no_record(self):
        self.assertIsNone(self._rec({"editor.fontSize": 13, "workbench.colorTheme": "Dark"}))

    def test_global_autoapprove_is_bypass(self):
        self.assertEqual(self._rec({"chat.tools.global.autoApprove": True})["permission_mode"], "bypassPermissions")
        self.assertEqual(self._rec({"chat.tools.autoApprove": True})["permission_mode"], "bypassPermissions")

    def test_file_autoapprove_is_accept_edits(self):
        rec = self._rec({"github.copilot.chat.agent.autoApproveFileChanges": True})
        self.assertEqual(rec["permission_mode"], "acceptEdits")

    def test_default_mode_when_nothing_auto_approved(self):
        rec = self._rec({"chat.agent.enabled": True})
        self.assertEqual(rec["permission_mode"], "default")
        # a False global auto-approve must NOT read as bypass
        self.assertEqual(self._rec({"chat.tools.global.autoApprove": False})["permission_mode"], "default")

    def test_terminal_allow_deny_and_regex_stripped(self):
        rec = self._rec({"chat.tools.terminal.autoApprove": {
            "/^git\\s+status/": True, "rm": False, "curl": False,
        }})
        self.assertIn("Bash(^git\\s+status *)", rec["allow_rules"])
        self.assertIn("Bash(rm *)", rec["deny_rules"])
        self.assertIn("Bash(curl *)", rec["deny_rules"])

    def test_blocklist_becomes_deny_rules(self):
        rec = self._rec({"github.copilot.chat.agent.terminalCommands.blocklist": ["sudo", "rm -rf"]})
        self.assertEqual(rec["permission_mode"], "default")
        self.assertIn("Bash(sudo *)", rec["deny_rules"])
        self.assertIn("Bash(rm -rf *)", rec["deny_rules"])

    def test_mcp_allow_deny_strings_and_objects(self):
        rec = self._rec({
            "chat.mcp.allowedServers": ["filesystem", {"name": "github-mcp"}, {"url": "https://x"}],
            "chat.mcp.deniedServers": ["shady"],
        })
        self.assertEqual(rec["mcp_tool_allowlist"], ["filesystem", "github-mcp", "https://x"])
        self.assertEqual(rec["mcp_policies"]["deniedMcpServers"], ["shady"])

    def test_sandbox_on_off(self):
        self.assertTrue(self._rec({"chat.agent.sandbox.enabled": "on"})["sandbox_enabled"])
        self.assertFalse(self._rec({"chat.agent.sandbox.enabled": "off"})["sandbox_enabled"])
        self.assertIsNone(self._rec({"chat.agent.enabled": True})["sandbox_enabled"])

    def test_raw_settings_excludes_noise(self):
        rec = self._rec({"chat.tools.global.autoApprove": True, "editor.fontSize": 13})
        self.assertIn("chat.tools.global.autoApprove", rec["raw_settings"])
        self.assertNotIn("editor.fontSize", rec["raw_settings"])

    def test_workspace_merge_escalates_mode_and_unions_rules(self):
        user = self._rec({"chat.tools.terminal.autoApprove": {"ls": True}})
        ws = self._rec({"chat.tools.global.autoApprove": True,
                        "chat.tools.terminal.autoApprove": {"pwd": True, "ls": True}})
        merged = self.ex._merge_workspace_records(user, [ws])
        # a workspace bypass surfaces; allow rules union without duplicating "ls"
        self.assertEqual(merged["permission_mode"], "bypassPermissions")
        self.assertEqual(sorted(merged["allow_rules"]), ["Bash(ls *)", "Bash(pwd *)"])

    def test_jsonc_comments_and_trailing_commas_parse(self):
        tmp = Path(tempfile.mkdtemp()) / "settings.json"
        tmp.write_text('{\n  // comment\n  "chat.tools.global.autoApprove": true,\n}\n', encoding="utf-8")
        try:
            parsed = self.ex._parse_jsonc(tmp)
            self.assertEqual(parsed.get("chat.tools.global.autoApprove"), True)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)


class TestMacOSExtractorOverFixture(unittest.TestCase):
    """Drive the real macOS extractor over a planted user + workspace settings.json."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-perm-e2e-", dir=str(Path.home())))
        user_dir = self.home / "Library" / "Application Support" / "Code" / "User"
        user_dir.mkdir(parents=True)
        (user_dir / "settings.json").write_text(json.dumps({
            "chat.tools.global.autoApprove": True,
            "chat.tools.terminal.autoApprove": {"git status": True, "rm": False},
        }), encoding="utf-8")
        # a workspace-scope settings.json
        ws = self.home / "repo" / ".vscode"
        ws.mkdir(parents=True)
        (ws / "settings.json").write_text(json.dumps({
            "chat.mcp.deniedServers": ["evil-mcp"],
        }), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_extracts_user_and_merges_workspace(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)  # constrain to the fixture
        rec = ex.extract_settings()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Bash(rm *)", rec["deny_rules"])
        # workspace-scoped MCP denial merged in
        self.assertIn("evil-mcp", rec["mcp_policies"]["deniedMcpServers"])

    def test_no_copilot_settings_returns_none(self):
        empty_home = Path(tempfile.mkdtemp(prefix="copilot-perm-empty-", dir=str(Path.home())))
        try:
            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(empty_home)
            self.assertIsNone(ex.extract_settings())
        finally:
            shutil.rmtree(empty_home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
