"""Tests for the VS Code GitHub Copilot settings/permission extractor.

Covers the permission mapping (settings.json keys → backend-ready record) and the
real per-OS extractor over planted settings.json fixtures.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.coding_tool_base import (  # noqa: E402
    _VSCODE_SETTINGS_MAX_BYTES,
    BaseGitHubCopilotSettingsExtractor,
)
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

    def test_mcp_governance_kept_as_context_not_a_permission_field(self):
        # chat.mcp.* is VS Code-wide MCP governance, not a Copilot tool permission:
        # captured in raw_settings for context, never promoted to its own field.
        rec = self._rec({
            "chat.mcp.access": "registry",
            "chat.mcp.allowedServers": [{"serverName": "github-mcp"}],
            "chat.mcp.deniedServers": [{"serverName": "shady"}],
        })
        self.assertEqual(rec["raw_settings"]["chat.mcp.access"], "registry")
        self.assertIn("chat.mcp.allowedServers", rec["raw_settings"])
        self.assertNotIn("mcp_tool_allowlist", rec)
        self.assertNotIn("mcp_policies", rec)

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
        # a named profile that blocks an extra terminal command
        prof = user_dir / "profiles" / "workp"
        prof.mkdir(parents=True)
        (prof / "settings.json").write_text(json.dumps({
            "chat.tools.terminal.autoApprove": {"curl": False},
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
        # the named profile's own deny rule is merged in too
        self.assertIn("Bash(curl *)", rec["deny_rules"])

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
            # only the in-home profile remains — the escaping YOLO is dropped
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


class TestTerminalRuleEdgeCases(unittest.TestCase):
    """Reachable shapes of chat.tools.terminal.autoApprove."""

    def setUp(self):
        self.ex = _MapExtractor()

    def _rules(self, auto):
        rec = self.ex._build_record({"chat.tools.terminal.autoApprove": auto},
                                    Path("/x/settings.json"), "user")
        return (rec or {}).get("allow_rules", []), (rec or {}).get("deny_rules", [])

    def test_object_form_approve_true_is_an_allow_rule(self):
        # VS Code also accepts {"approve": true, "matchCommandLine": true}
        allow, _ = self._rules({"npm run": {"approve": True, "matchCommandLine": True}})
        self.assertIn("Bash(npm run *)", allow)

    def test_object_form_approve_false_is_a_deny_rule(self):
        # the dangerous miss: an object-form denial must not vanish
        _, deny = self._rules({"rm -rf": {"approve": False}})
        self.assertIn("Bash(rm -rf *)", deny)

    def test_unknown_verdict_shape_is_ignored_not_guessed(self):
        allow, deny = self._rules({"weird": "yes", "other": 123, "none": None})
        self.assertEqual(allow, [])
        self.assertEqual(deny, [])

    def test_empty_pattern_key_is_skipped(self):
        allow, _ = self._rules({"": True, "ls": True})
        self.assertEqual(allow, ["Bash(ls *)"])

    def test_unicode_and_quoted_patterns_survive(self):
        allow, _ = self._rules({'echo "héllo"': True})
        self.assertIn('Bash(echo "héllo" *)', allow)

    def test_duplicate_rules_deduped(self):
        rec = self.ex._build_record({"chat.tools.terminal.autoApprove": {"/ls/": True, "ls": True}},
                                    Path("/x/s.json"), "user")
        self.assertEqual(rec["allow_rules"], ["Bash(ls *)"])


class TestModeEdgeCases(unittest.TestCase):
    def setUp(self):
        self.ex = _MapExtractor()

    def _mode(self, data):
        return self.ex._build_record(data, Path("/x/s.json"), "user")["permission_mode"]

    def test_truthy_non_boolean_is_not_bypass(self):
        # only a literal true flips the mode; "true"/1 must not be read as bypass
        self.assertEqual(self._mode({"chat.tools.global.autoApprove": "true"}), "default")
        self.assertEqual(self._mode({"chat.tools.global.autoApprove": 1}), "default")

    def test_legacy_key_alone_still_bypasses(self):
        self.assertEqual(self._mode({"chat.tools.autoApprove": True}), "bypassPermissions")


class TestParseResilience(unittest.TestCase):
    """A broken or hostile settings.json degrades quietly; it never crashes a scan."""

    def setUp(self):
        self.ex = _MapExtractor()
        self.tmp = Path(tempfile.mkdtemp(prefix="parse-"))

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            try: p.chmod(0o644)
            except OSError: pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        p = self.tmp / "settings.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_malformed_json_returns_none(self):
        self.assertIsNone(self.ex._parse_jsonc(self._write('{"a": ')))

    def test_non_dict_json_returns_none(self):
        self.assertIsNone(self.ex._parse_jsonc(self._write('["not", "a", "dict"]')))
        self.assertIsNone(self.ex._parse_jsonc(self._write('42')))

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.ex._parse_jsonc(self.tmp / "nope.json"))

    def test_empty_object_yields_no_record(self):
        self.assertIsNone(self.ex._build_record({}, Path("/x/s.json"), "user"))

    @unittest.skipUnless(os.name == "posix", "chmod 000 is POSIX-specific")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses file permissions, so chmod 000 is not unreadable")
    def test_unreadable_file_returns_none(self):
        p = self._write('{"chat.tools.global.autoApprove": true}')
        os.chmod(p, 0o000)
        try:
            self.assertIsNone(self.ex._parse_jsonc(p))
        finally:
            os.chmod(p, 0o644)


class TestChannelsAndProfiles(unittest.TestCase):
    """Stable vs Insiders, many profiles, and hostile entries under profiles/."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="chan-", dir=str(Path.home())))
        self.stable = self.home / "Library" / "Application Support" / "Code" / "User"
        self.insiders = self.home / "Library" / "Application Support" / "Code - Insiders" / "User"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _ex(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        return ex

    @staticmethod
    def _w(p, obj):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj), encoding="utf-8")

    def test_insiders_only_install_is_extracted(self):
        # no stable dir at all — the Insiders channel must still be read
        self._w(self.insiders / "settings.json", {"chat.tools.global.autoApprove": True})
        rec = self._ex().extract_settings()
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        self.assertIn("Code - Insiders", rec["settings_path"])

    def test_riskiest_channel_wins_and_is_attributed(self):
        # benign stable + YOLO Insiders: the risk must surface, attributed to Insiders
        self._w(self.stable / "settings.json", {"chat.agent.enabled": True})
        self._w(self.insiders / "settings.json", {"chat.tools.global.autoApprove": True})
        rec = self._ex().extract_settings()
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        self.assertIn("Code - Insiders", rec["settings_path"],
                      "settings_path must point at the channel that carries the risk")

    def test_many_profiles_all_merge_deterministically(self):
        self._w(self.stable / "settings.json", {"chat.agent.enabled": True})
        for i in range(20):
            self._w(self.stable / "profiles" / f"p{i:02d}" / "settings.json",
                    {"chat.tools.terminal.autoApprove": {f"cmd{i:02d}": True}})
        first = self._ex().extract_settings()
        second = self._ex().extract_settings()
        self.assertEqual(len(first["allow_rules"]), 20, "every profile's rule must merge")
        self.assertEqual(first["allow_rules"], second["allow_rules"], "order must be deterministic")

    def test_stray_file_under_profiles_is_ignored(self):
        self._w(self.stable / "settings.json", {"chat.tools.global.autoApprove": True})
        (self.stable / "profiles").mkdir(parents=True, exist_ok=True)
        (self.stable / "profiles" / "not-a-dir.txt").write_text("junk", encoding="utf-8")
        self.assertEqual(self._ex().extract_settings()["permission_mode"], "bypassPermissions")

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_symlinked_profile_dir_escaping_home_is_refused(self):
        # the profile DIR (not just the leaf) is a link out of the home
        outside = Path(tempfile.mkdtemp(prefix="chan-outside-"))
        try:
            (outside / "settings.json").write_text(
                json.dumps({"chat.tools.global.autoApprove": True}), encoding="utf-8")
            self._w(self.stable / "settings.json", {"chat.agent.enabled": True})
            (self.stable / "profiles").mkdir(parents=True, exist_ok=True)
            os.symlink(outside, self.stable / "profiles" / "escape")
            rec = self._ex().extract_settings()
            self.assertNotEqual(rec["permission_mode"], "bypassPermissions",
                                "settings reached through an escaping profile dir must not be read")
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestContainmentRace(unittest.TestCase):
    """A settings file swapped for an out-of-home link between enumeration and the
    read must never be reported. Validating a path and opening it separately leaves
    that window; the read validates the descriptor it actually holds."""

    @unittest.skipUnless(os.name == "posix", "symlink swap is POSIX-specific")
    def test_file_swapped_after_enumeration_is_not_read(self):
        home = Path(tempfile.mkdtemp(prefix="race-home-", dir=str(Path.home())))
        outside = Path(tempfile.mkdtemp(prefix="race-outside-"))
        try:
            (outside / "secret.json").write_text(
                json.dumps({"chat.tools.global.autoApprove": True,
                            "chat.mcp.access": "OUT-OF-HOME"}), encoding="utf-8")
            ud = home / "Library" / "Application Support" / "Code" / "User"
            ud.mkdir(parents=True)
            (ud / "settings.json").write_text(
                json.dumps({"chat.agent.enabled": True}), encoding="utf-8")

            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(home)
            original = ex._iter_user_settings_files

            def swap_then_yield(user_home):
                for p in list(original(user_home)):
                    if Path(p).name == "settings.json" and Path(p).parent == ud:
                        os.unlink(p)                                  # attacker wins the window
                        os.symlink(outside / "secret.json", p)
                    yield p

            ex._iter_user_settings_files = swap_then_yield
            rec = ex.extract_settings()
            self.assertNotIn("OUT-OF-HOME", json.dumps(rec or {}),
                             "content behind an out-of-home link must never be reported")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


class TestOwnershipContainment(unittest.TestCase):
    """A user's settings file must belong to that user. A hard link keeps its
    target's owner while its path stays inside the home, so path containment is
    blind to it — ownership is what refuses a link to a foreign file."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="own-home-", dir=str(Path.home())))
        self.ud = self.home / "Library" / "Application Support" / "Code" / "User"
        self.ud.mkdir(parents=True)
        self.ud.joinpath("settings.json").write_text(
            json.dumps({"chat.tools.global.autoApprove": True}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _ex(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        return ex

    def test_same_owner_file_is_read(self):
        # the ordinary case must keep working
        self.assertEqual(self._ex().extract_settings()["permission_mode"], "bypassPermissions")

    @unittest.skipUnless(os.name == "posix", "uid ownership is POSIX-specific")
    def test_file_owned_by_another_user_is_refused(self):
        # simulate the home belonging to a different uid than the settings file,
        # which is exactly the state a hard link to a foreign file produces
        real_stat = os.stat
        home_real = os.path.realpath(str(self.home))

        class _FakeStat:
            def __init__(self, base): self._b = base
            def __getattr__(self, name): return getattr(self._b, name)
            @property
            def st_uid(self): return self._b.st_uid + 1  # a different owner

        def patched(p, *a, **k):
            st = real_stat(p, *a, **k)
            return _FakeStat(st) if os.path.realpath(str(p)) == home_real else st

        os.stat = patched
        try:
            self.assertIsNone(self._ex().extract_settings(),
                              "a settings file not owned by the home's user must be refused")
        finally:
            os.stat = real_stat


class TestTerminalMasterSwitch(unittest.TestCase):
    """chat.tools.terminal.enableAutoApprove gates the whole terminal feature."""

    def setUp(self):
        self.ex = _MapExtractor()
        self.auto = {"npm run build": True, "rm": False}

    def _rules(self, data):
        rec = self.ex._build_record(data, Path("/x/settings.json"), "user")
        return (rec or {}).get("allow_rules", []), (rec or {}).get("deny_rules", [])

    def test_switch_off_drops_allow_rules_but_keeps_denies(self):
        allow, deny = self._rules({"chat.tools.terminal.autoApprove": self.auto,
                                   "chat.tools.terminal.enableAutoApprove": False})
        self.assertEqual(allow, [], "nothing is auto-approved while the switch is off")
        self.assertEqual(deny, ["Bash(rm *)"])

    def test_switch_absent_keeps_allow_rules(self):
        # the setting defaults to true in VS Code, so absence means enabled
        allow, _ = self._rules({"chat.tools.terminal.autoApprove": self.auto})
        self.assertEqual(allow, ["Bash(npm run build *)"])

    def test_switch_on_keeps_allow_rules(self):
        allow, _ = self._rules({"chat.tools.terminal.autoApprove": self.auto,
                                "chat.tools.terminal.enableAutoApprove": True})
        self.assertEqual(allow, ["Bash(npm run build *)"])


class TestPermissionLevelAndEdits(unittest.TestCase):
    """chat.permissions.default (the permissions picker) and edit auto-approval."""

    def setUp(self):
        self.ex = _MapExtractor()

    def _mode(self, data):
        return self.ex._build_record(data, Path("/x/settings.json"), "user")["permission_mode"]

    def test_auto_approve_level_is_bypass(self):
        self.assertEqual(self._mode({"chat.permissions.default": "autoApprove"}),
                         "bypassPermissions")

    def test_autopilot_level_is_bypass(self):
        self.assertEqual(self._mode({"chat.permissions.default": "autopilot"}),
                         "bypassPermissions")

    def test_default_level_stays_default(self):
        self.assertEqual(self._mode({"chat.permissions.default": "default"}), "default")

    def test_edit_auto_approve_is_accept_edits(self):
        self.assertEqual(self._mode({"chat.tools.edits.autoApprove": {"**/*": True}}),
                         "acceptEdits")

    def test_edit_deny_only_stays_default(self):
        self.assertEqual(self._mode({"chat.tools.edits.autoApprove": {"**/.env": False}}),
                         "default")

    def test_global_bypass_outranks_accept_edits(self):
        self.assertEqual(self._mode({"chat.tools.edits.autoApprove": {"**/*": True},
                                     "chat.tools.global.autoApprove": True}),
                         "bypassPermissions")


class TestNonRegularFiles(unittest.TestCase):
    """A FIFO planted at settings.json must not stall a privileged scan."""

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are POSIX-only")
    def test_fifo_is_skipped_without_blocking(self):
        home = Path(tempfile.mkdtemp(prefix="fifo-home-", dir=str(Path.home())))
        try:
            ud = home / "Library" / "Application Support" / "Code" / "User"
            ud.mkdir(parents=True)
            os.mkfifo(str(ud / "settings.json"))

            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(home)
            result = {}
            worker = threading.Thread(target=lambda: result.setdefault("r", ex.extract_settings()))
            worker.daemon = True
            worker.start()
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive(), "a planted FIFO hung the scan")
            self.assertIsNone(result.get("r"))
        finally:
            shutil.rmtree(home, ignore_errors=True)


class TestHardLinkContainment(unittest.TestCase):
    """A hard link keeps its target's contents while its path sits in the home."""

    def test_hard_link_to_outside_file_is_refused(self):
        home = Path(tempfile.mkdtemp(prefix="link-home-", dir=str(Path.home())))
        outside = Path(tempfile.mkdtemp(prefix="link-outside-"))
        try:
            victim = outside / "victim.json"
            victim.write_text(json.dumps({"chat.tools.global.autoApprove": True,
                                          "chat.mcp.access": "OUT-OF-HOME"}), encoding="utf-8")
            ud = home / "Library" / "Application Support" / "Code" / "User"
            ud.mkdir(parents=True)
            try:
                os.link(str(victim), str(ud / "settings.json"))
            except (OSError, NotImplementedError, AttributeError) as e:
                self.skipTest(f"hard links unavailable here: {e}")

            ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: cb(home)
            self.assertNotIn("OUT-OF-HOME", json.dumps(ex.extract_settings() or {}),
                             "content behind a hard link must never be reported")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


class TestSettingsSizeCap(unittest.TestCase):
    """A planted multi-GB settings.json must not be slurped into memory during a
    privileged scan — without starving a genuinely large real one."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="cap-home-", dir=str(Path.home())))
        self.ud = self.home / "Library" / "Application Support" / "Code" / "User"
        self.ud.mkdir(parents=True)
        self.settings = self.ud / "settings.json"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _ex(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        return ex

    def test_oversized_file_is_refused(self):
        self.settings.write_text(json.dumps({"chat.tools.global.autoApprove": True}),
                                 encoding="utf-8")
        # sparse: st_size is what the cap reads, so no gigabyte is actually written
        os.truncate(str(self.settings), _VSCODE_SETTINGS_MAX_BYTES + 1)
        self.assertIsNone(self._ex().extract_settings())

    def test_large_real_settings_is_still_read(self):
        # ~2 MB of legitimate content: well past anything real, still under the cap
        padding = {f"chat.tools.terminal.autoApprove-note-{i}": "x" * 200 for i in range(10000)}
        padding["chat.tools.global.autoApprove"] = True
        self.settings.write_text(json.dumps(padding), encoding="utf-8")
        self.assertGreater(self.settings.stat().st_size, 2 * 1024 * 1024)
        self.assertLess(self.settings.stat().st_size, _VSCODE_SETTINGS_MAX_BYTES)
        self.assertEqual(self._ex().extract_settings()["permission_mode"], "bypassPermissions")


if __name__ == "__main__":
    unittest.main()
