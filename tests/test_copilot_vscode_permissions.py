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

    def _user_config_dirs(self, user_home):
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

    def test_only_registry_verified_keys_are_captured(self):
        # Guard against re-introducing blog-era phantom keys: keys that VS Code
        # does not register must never appear in raw_settings or affect the mode.
        rec = self._rec({
            "chat.tools.global.autoApprove": True,                              # real
            "github.copilot.chat.agent.autoApproveFileChanges": True,           # phantom
            "github.copilot.chat.agent.terminalCommands.blocklist": ["sudo"],   # phantom
        })
        self.assertIn("chat.tools.global.autoApprove", rec["raw_settings"])
        self.assertNotIn("github.copilot.chat.agent.autoApproveFileChanges", rec["raw_settings"])
        self.assertNotIn("github.copilot.chat.agent.terminalCommands.blocklist", rec["raw_settings"])

    def test_mcp_allow_deny_real_entry_shapes(self):
        # VS Code entries match by serverName / serverUrl / serverCommand (or a bare
        # string). Each shape resolves to its identity in the policy lists.
        rec = self._rec({
            "chat.mcp.allowedServers": ["filesystem", {"serverName": "github-mcp"},
                                        {"serverUrl": "https://mcp.contoso.com/*"},
                                        {"serverCommand": ["/usr/local/bin/legacy-mcp", "--stdio"]}],
            "chat.mcp.deniedServers": [{"serverName": "shady"}],
        })
        self.assertEqual(
            rec["mcp_tool_allowlist"],
            ["filesystem", "github-mcp", "https://mcp.contoso.com/*", "/usr/local/bin/legacy-mcp"],
        )
        self.assertEqual(rec["mcp_policies"]["deniedMcpServers"], ["shady"])

    def test_sandbox_on_off(self):
        self.assertTrue(self._rec({"chat.agent.sandbox.enabled": "on"})["sandbox_enabled"])
        self.assertFalse(self._rec({"chat.agent.sandbox.enabled": "off"})["sandbox_enabled"])
        self.assertIsNone(self._rec({"chat.agent.enabled": True})["sandbox_enabled"])

    def test_raw_settings_excludes_noise(self):
        rec = self._rec({"chat.tools.global.autoApprove": True, "editor.fontSize": 13})
        self.assertIn("chat.tools.global.autoApprove", rec["raw_settings"])
        self.assertNotIn("editor.fontSize", rec["raw_settings"])

    def test_merge_escalates_mode_and_unions_rules(self):
        user = self._rec({"chat.tools.terminal.autoApprove": {"ls": True}})
        ws = self._rec({"chat.tools.global.autoApprove": True,
                        "chat.tools.terminal.autoApprove": {"pwd": True, "ls": True}})
        merged = self.ex._merge_records(user, [ws])
        # a more-permissive profile surfaces; allow rules union without duplicating "ls"
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
    """Drive the real macOS extractor over planted user + profile settings.json."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-perm-e2e-", dir=str(Path.home())))
        user_dir = self.home / "Library" / "Application Support" / "Code" / "User"
        user_dir.mkdir(parents=True)
        (user_dir / "settings.json").write_text(json.dumps({
            "chat.tools.global.autoApprove": True,
            "chat.tools.terminal.autoApprove": {"git status": True, "rm": False},
        }), encoding="utf-8")
        # a named profile that denies an MCP server
        prof = user_dir / "profiles" / "workp"
        prof.mkdir(parents=True)
        (prof / "settings.json").write_text(json.dumps({
            "chat.mcp.deniedServers": ["evil-mcp"],
        }), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_extracts_user_and_merges_profiles(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)  # constrain to the fixture
        rec = ex.extract_settings()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Bash(rm *)", rec["deny_rules"])
        # profile-scoped MCP denial merged in
        self.assertIn("evil-mcp", rec["mcp_policies"]["deniedMcpServers"])

    def test_named_profile_yolo_is_detected_and_escalates(self):
        # A locked-down default profile but a YOLO *named* profile must surface:
        # the profile is its own permission surface (profiles/<id>/settings.json).
        base = self.home / "Library" / "Application Support" / "Code" / "User"
        # default profile: benign
        (base / "settings.json").write_text(json.dumps({"chat.agent.enabled": True}), encoding="utf-8")
        # named profile: YOLO
        prof = base / "profiles" / "ab12cd"
        prof.mkdir(parents=True)
        (prof / "settings.json").write_text(json.dumps({
            "chat.tools.global.autoApprove": True,
            "chat.tools.terminal.autoApprove": {"rm": False},
        }), encoding="utf-8")
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        rec = ex.extract_settings()
        self.assertEqual(rec["permission_mode"], "bypassPermissions", "a YOLO named profile must surface")
        self.assertIn("Bash(rm *)", rec.get("deny_rules", []))

    def test_multi_user_scan_surfaces_the_riskiest_user(self):
        # Elevated scan over two users: a benign user first, a YOLO user second.
        # The returned record must be the YOLO user's (its own settings_path), so
        # the risky posture is never hidden behind the benign first user.
        homes = []
        for name, settings in (("benign", {"chat.agent.enabled": True}),
                               ("yolo", {"chat.tools.global.autoApprove": True})):
            h = Path(tempfile.mkdtemp(prefix=f"copilot-{name}-", dir=str(Path.home())))
            ud = h / "Library" / "Application Support" / "Code" / "User"
            ud.mkdir(parents=True)
            (ud / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            homes.append(h)
        try:
            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: [cb(h) for h in homes]
            rec = ex.extract_settings()
            self.assertEqual(rec["permission_mode"], "bypassPermissions")
            self.assertIn("yolo", rec["settings_path"], "riskiest user's own settings_path must be preserved")
        finally:
            for h in homes:
                shutil.rmtree(h, ignore_errors=True)

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_settings_symlink_escaping_home_is_refused(self):
        # A user's settings.json symlinked to an out-of-home file must NOT be read
        # (a privileged scan could otherwise report root-owned content as the user's).
        outside = Path(tempfile.mkdtemp(prefix="copilot-outside-"))
        try:
            (outside / "evil.json").write_text(json.dumps({"chat.tools.global.autoApprove": True}), encoding="utf-8")
            ud = self.home / "Library" / "Application Support" / "Code" / "User"
            # replace the benign default settings.json with an escaping symlink
            (ud / "settings.json").unlink()
            os.symlink(outside / "evil.json", ud / "settings.json")
            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(self.home)
            rec = ex.extract_settings()
            # only the in-home profile (workp, mcp deny) remains — the escaping YOLO is dropped
            self.assertNotEqual(rec and rec.get("permission_mode"), "bypassPermissions",
                                "content from an out-of-home symlink must not be reported")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_in_home_settings_symlink_is_allowed(self):
        # A stow/chezmoi-style symlink that resolves INSIDE the home is still read.
        real = self.home / "dotfiles" / "vscode-settings.json"
        real.parent.mkdir(parents=True)
        real.write_text(json.dumps({"chat.tools.global.autoApprove": True}), encoding="utf-8")
        ud = self.home / "Library" / "Application Support" / "Code" / "User"
        (ud / "settings.json").unlink()
        os.symlink(real, ud / "settings.json")
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        self.assertEqual(ex.extract_settings()["permission_mode"], "bypassPermissions")

    def test_no_copilot_settings_returns_none(self):
        empty_home = Path(tempfile.mkdtemp(prefix="copilot-perm-empty-", dir=str(Path.home())))
        try:
            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(empty_home)
            self.assertIsNone(ex.extract_settings())
        finally:
            shutil.rmtree(empty_home, ignore_errors=True)


class TestBackendShapeParity(unittest.TestCase):
    """The record must stay within the Cursor record's vocabulary — that is the
    shape gateway-data's AIToolPermissions ingest and the fe already accept, so
    routing Copilot permissions needs no backend/frontend change."""

    # From BaseCursorSettingsExtractor._parse_composer_state (the backend contract).
    CURSOR_RECORD_KEYS = {
        "settings_source", "scope", "settings_path", "raw_settings", "permission_mode",
        "sandbox_enabled", "allow_rules", "deny_rules", "mcp_tool_allowlist",
        "mcp_servers", "mcp_policies",
    }
    REQUIRED = {"permission_mode", "settings_source", "settings_path"}

    def test_record_shape_within_cursor_vocabulary(self):
        rec = _MapExtractor()._build_record({
            "chat.tools.global.autoApprove": True,
            "chat.tools.terminal.autoApprove": {"rm": False},
            "chat.mcp.allowedServers": ["x"], "chat.mcp.deniedServers": ["y"],
            "chat.agent.sandbox.enabled": "on",
        }, Path("/s.json"), "user")
        extra = set(rec) - self.CURSOR_RECORD_KEYS
        self.assertEqual(extra, set(), f"keys outside the backend-accepted shape: {extra}")
        self.assertTrue(self.REQUIRED.issubset(set(rec)), "missing a required backend field")


class TestCanonicalRowAttachment(unittest.TestCase):
    """Permissions attach to exactly the canonical VS Code row and no other — a
    multi-row install must not double-report, and non-Copilot tools are untouched
    (the record is freshly built, never shared)."""

    def _detector(self):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._github_copilot_rules_extractor = None      # branch guards None → skipped
        det._github_copilot_mcp_extractor = None
        det._get_copilot_cli_skills = lambda: {"user_skills": [], "project_skills": []}
        det._github_copilot_settings_extractor.extract_settings = lambda: {
            "permission_mode": "bypassPermissions", "settings_source": "user",
            "scope": "user", "settings_path": "/x", "raw_settings": {},
        }
        det._canonical_vscode_copilot = "github copilot chat (vs code)"
        return det

    def test_only_canonical_row_gets_permissions(self):
        det = self._detector()
        chat = det.process_single_tool(
            {"name": "GitHub Copilot Chat (VS Code)", "version": "1", "install_path": "/a", "projects": []})
        plain = det.process_single_tool(
            {"name": "GitHub Copilot (VS Code)", "version": "1", "install_path": "/b", "projects": []})
        self.assertIn("permissions", chat)
        self.assertEqual(chat["permissions"]["permission_mode"], "bypassPermissions")
        self.assertNotIn("permissions", plain, "a non-canonical Copilot row must not double-attach")


if __name__ == "__main__":
    unittest.main()
