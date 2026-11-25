"""Organization-deployed Claude Code policy reaches every user's report.

An organization puts Claude Code policy in a system directory, outside every
user's home: settings, rules (CLAUDE.md) and an MCP server list. Each piece was
lost somewhere on its way to a report:

* Linux never read the settings file or CLAUDE.md, and reported "no managed rules".
* The MCP list was read on every OS, then dropped by the per-user split, because
  its path is under no user's home.
* An empty MCP list, which is how MCP is turned off, was never recorded.

These run the real process_single_tool -> per-user report path with two users
under a simulated root scan, against real files on disk.
"""

import json
import os
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.ai_tools_discovery as atd
import scripts.coding_discovery_tools.mcp_extraction_helpers as mcp_helpers
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.linux.claude_code.claude_rules_extractor import LinuxClaudeRulesExtractor
from scripts.coding_discovery_tools.linux.claude_code.settings_extractor import LinuxClaudeSettingsExtractor

USERS = ("alice", "bob")


class _ManagedPolicyCase(unittest.TestCase):
    """Two Linux user homes and a stand-in for /etc/claude-code."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="managed-policy-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scanner_home = self.tmp / "root"
        self.scanner_home.mkdir()
        self.homes = {u: self.tmp / "home" / u for u in USERS}
        for home in self.homes.values():
            home.mkdir(parents=True)
        self.etc = self.tmp / "etc" / "claude-code"
        self.etc.mkdir(parents=True)
        self.managed_mcp = self.etc / "managed-mcp.json"

        patches = [
            mock.patch.dict(os.environ, {"HOME": str(self.scanner_home), "AI_DISCOVERY_SENTRY_DSN": ""}),
            mock.patch.object(utils_mod, "_SENTRY_DSN", ""),
            mock.patch.object(atd, "get_all_users_linux", return_value=list(USERS)),
            mock.patch.object(atd, "linux_home_for_user", side_effect=lambda u: self.homes[u]),
            mock.patch(
                "scripts.coding_discovery_tools.linux_extraction_helpers.get_linux_user_homes",
                return_value=list(self.homes.values()),
            ),
            mock.patch(
                "scripts.coding_discovery_tools.linux.claude_code.settings_extractor.get_linux_user_homes",
                return_value=list(self.homes.values()),
            ),
            mock.patch(
                "scripts.coding_discovery_tools.linux.claude_code.claude_rules_extractor.get_linux_user_homes",
                return_value=list(self.homes.values()),
            ),
            mock.patch.object(mcp_helpers, "get_managed_mcp_path", return_value=self.managed_mcp),
            mock.patch.object(atd, "get_managed_mcp_path", return_value=self.managed_mcp, create=True),
            mock.patch.object(LinuxClaudeSettingsExtractor, "MANAGED_SETTINGS_PATH", self.etc / "managed-settings.json",
                              create=True),
            mock.patch.object(LinuxClaudeSettingsExtractor, "MANAGED_DROPIN_DIR", self.etc / "managed-settings.d", create=True),
            mock.patch.object(LinuxClaudeRulesExtractor, "MANAGED_RULES_PATH", self.etc / "CLAUDE.md", create=True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.detector = AIToolsDetector(os_name="Linux")

    def _write_json(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _report_for(self, user: str, tool: dict, install_path: str = None) -> dict:
        """The report row a real scan builds for one user, or None when they get no row."""
        detected = {"name": "Claude Code", "install_path": install_path or str(self.homes[user] / ".local/bin/claude")}
        processed = self.detector.process_single_tool(dict(detected))
        manifest = {atd._install_key(user, detected)}
        return atd._tool_for_user(self.detector, detected, processed, user, self.homes[user], manifest)

    def _managed_mcp_projects(self, report: dict) -> list:
        return [p for p in (report or {}).get("projects", []) if p.get("path") == str(self.managed_mcp)]


class TheOrganizationMcpListReachesEveryUser(_ManagedPolicyCase):

    def test_every_user_gets_the_deployed_servers(self):
        self._write_json(self.managed_mcp, {"mcpServers": {"corp-gateway": {"command": "corp-mcp"}}})
        for user in USERS:
            with self.subTest(user=user):
                managed = self._managed_mcp_projects(self._report_for(user, {}))
                self.assertEqual(
                    [s.get("name") for p in managed for s in p.get("mcpServers", [])], ["corp-gateway"],
                    "the organization MCP list was read and then dropped from this user's report",
                )

    def test_an_empty_list_is_reported_as_mcp_turned_off(self):
        self._write_json(self.managed_mcp, {"mcpServers": {}})
        managed = self._managed_mcp_projects(self._report_for("alice", {}))
        self.assertEqual(len(managed), 1, "an organization that turned MCP off looks like one with no policy")
        self.assertEqual(managed[0].get("mcpServers"), [])

    def test_no_file_reports_nothing(self):
        self.assertEqual(self._managed_mcp_projects(self._report_for("alice", {})), [])

    def test_the_report_carries_only_fields_the_backend_accepts(self):
        """The backend rejects a whole report over one unknown project field."""
        self._write_json(self.managed_mcp, {"mcpServers": {"corp-gateway": {"command": "corp-mcp"}}})
        allowed = {"path", "rules", "skills", "mcpServers", "additionalMcpData", "hooks"}
        for project in self._report_for("alice", {}).get("projects", []):
            self.assertLessEqual(set(project), allowed, f"unexpected project fields: {set(project) - allowed}")

    def test_another_users_projects_still_stay_out(self):
        self._write_json(self.managed_mcp, {"mcpServers": {"corp-gateway": {"command": "corp-mcp"}}})
        self._write_json(self.homes["bob"] / "proj" / ".mcp.json", {"mcpServers": {"bob-only": {"command": "b"}}})
        alice = self._report_for("alice", {})
        names = {s.get("name") for p in alice.get("projects", []) for s in p.get("mcpServers", [])}
        self.assertNotIn("bob-only", names)
        self.assertIn("corp-gateway", names)


class LinuxReadsTheOrganizationSettingsAndRules(_ManagedPolicyCase):

    def test_the_settings_file_and_drop_ins_are_read(self):
        self._write_json(self.etc / "managed-settings.json", {"deniedMcpServers": [{"serverName": "evil"}]})
        self._write_json(self.etc / "managed-settings.d" / "10-perms.json", {"permissions": {"deny": ["Bash(rm:*)"]}})
        settings = LinuxClaudeSettingsExtractor().extract_settings() or []
        by_scope = {s["scope"]: s for s in settings}
        self.assertEqual(by_scope["managed"]["mcp_policies"]["deniedMcpServers"], [{"serverName": "evil"}])
        self.assertEqual(by_scope["managed_dropin"]["permissions"]["deny"], ["Bash(rm:*)"])

    def test_the_organization_settings_reach_every_user(self):
        self._write_json(self.etc / "managed-settings.json", {"permissions": {"deny": ["WebFetch"]}})
        for user in USERS:
            with self.subTest(user=user):
                perms = self._report_for(user, {}).get("permissions") or {}
                self.assertEqual(perms.get("settings_source"), "managed", perms)

    def test_a_users_own_settings_are_not_replaced(self):
        self._write_json(self.etc / "managed-settings.json", {"permissions": {"deny": ["WebFetch"]}})
        self._write_json(self.homes["alice"] / ".claude" / "settings.json", {"permissions": {"allow": ["Bash(ls)"]}})
        alice = self._report_for("alice", {}).get("permissions") or {}
        bob = self._report_for("bob", {}).get("permissions") or {}
        self.assertIn("Bash(ls)", alice.get("allow_rules") or [], "alice's own settings were lost")
        self.assertNotIn("Bash(ls)", bob.get("allow_rules") or [], "alice's settings leaked into bob's report")
        self.assertIn("WebFetch", bob.get("deny_rules") or [], "the organization settings did not reach bob")

    def test_broken_settings_json_is_survived(self):
        (self.etc / "managed-settings.json").write_text('{"permissions": ', encoding="utf-8")
        self.assertIsNone(LinuxClaudeSettingsExtractor().extract_settings())

    def test_the_organization_claude_md_is_reported_as_managed(self):
        (self.etc / "CLAUDE.md").write_text("Never push to personal repos.\n", encoding="utf-8")
        rules = LinuxClaudeRulesExtractor().extract_all_claude_rules()["managed_rules"]
        self.assertEqual([(r["file_path"], r["scope"]) for r in rules], [(str(self.etc / "CLAUDE.md"), "managed")])
        for user in USERS:
            with self.subTest(user=user):
                reported = [r for p in self._report_for(user, {}).get("projects", [])
                            for r in p.get("rules", []) if r.get("scope") == "managed"]
                self.assertEqual(len(reported), 1, "the organization rules did not reach this user")

    def test_organization_rules_reach_users_without_rules_of_their_own(self):
        """One user's personal rules must not narrow the organization rules down to that user."""
        (self.etc / "CLAUDE.md").write_text("Org rules.\n", encoding="utf-8")
        (self.homes["alice"] / ".claude").mkdir(parents=True)
        (self.homes["alice"] / ".claude" / "CLAUDE.md").write_text("alice's rules\n", encoding="utf-8")
        for user in USERS:
            with self.subTest(user=user):
                reported = [r for p in self._report_for(user, {}).get("projects", [])
                            for r in p.get("rules", []) if r.get("scope") == "managed"]
                self.assertEqual(len(reported), 1, "the organization rules did not reach this user exactly once")

    def test_no_claude_md_reports_no_managed_rules(self):
        self.assertEqual(LinuxClaudeRulesExtractor().extract_all_claude_rules()["managed_rules"], [])


class OrganizationPolicyAloneDoesNotMakeAUser(_ManagedPolicyCase):
    """A machine-wide binary owned by someone else gets a row only for users with data of their own."""

    def _disowned_report(self, user: str):
        with mock.patch.object(atd, "machine_global_binary_owned_by_user", return_value=False):
            return self._report_for(user, {}, install_path="/usr/local/bin/claude")

    def test_policy_only_gives_no_row(self):
        self._write_json(self.managed_mcp, {"mcpServers": {"corp-gateway": {"command": "corp-mcp"}}})
        (self.etc / "CLAUDE.md").write_text("Org rules.\n", encoding="utf-8")
        self.assertIsNone(self._disowned_report("bob"), "organization policy alone created a row for this user")

    def test_a_users_own_rule_still_gives_a_row(self):
        self._write_json(self.managed_mcp, {"mcpServers": {"corp-gateway": {"command": "corp-mcp"}}})
        (self.homes["bob"] / ".claude").mkdir(parents=True)
        (self.homes["bob"] / ".claude" / "CLAUDE.md").write_text("bob's rules\n", encoding="utf-8")
        report = self._disowned_report("bob")
        self.assertIsNotNone(report, "a user with their own rules lost their row")
        self.assertTrue(self._managed_mcp_projects(report), "the organization MCP list did not ride along")


class TheMcpExemptionHoldsOnEveryPlatform(unittest.TestCase):
    """The per-user split dropped the organization MCP list on macOS and Windows too."""

    def test_macos_and_windows_paths_survive_the_split(self):
        cases = (
            ("Darwin", "/Library/Application Support/ClaudeCode/managed-mcp.json", Path("/Users/alice")),
            ("Windows", "C:/Program Files/ClaudeCode/managed-mcp.json", Path("C:/Users/alice")),
        )
        for os_name, managed, home in cases:
            with self.subTest(os=os_name), mock.patch.object(platform, "system", return_value=os_name):
                tool = {"name": "Claude Code", "projects": [
                    {"path": managed, "mcpServers": [{"name": "corp"}]},
                    {"path": "/Users/bob/proj" if os_name == "Darwin" else "C:/Users/bob/proj",
                     "mcpServers": [{"name": "bob"}]},
                ]}
                kept = AIToolsDetector(os_name=os_name).filter_tool_projects_by_user(tool, home)
                self.assertEqual([p["path"] for p in kept["projects"]], [managed])


if __name__ == "__main__":
    unittest.main()
