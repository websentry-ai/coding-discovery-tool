"""
Tests for claude.ai native connector extraction.

``~/.claude/mcp-needs-auth-cache.json`` — the only source before this change —
holds just the connectors that are *pending authentication*, so every connector
the user has actually signed into was invisible. On a real machine the cache
listed 1 of 5 currently-connected connectors while ``~/.claude.json``'s
``claudeAiMcpEverConnected`` listed all 5.

Names are all these servers have locally (the config lives server-side), so the
union of both files is reported name-only. Tool lists exist in one other place:
the desktop app's session files, which record ``remoteMcpServersConfig`` with
each connector's full tool list. The terminal CLI never writes those, so tools
are attached when present and omitted otherwise.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.mcp_extraction_helpers as helpers
from scripts.coding_discovery_tools import utils
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_session(session_dir: Path, name: str, connectors, mtime: float, last_activity=None) -> Path:
    """Write one desktop session file carrying `remoteMcpServersConfig`."""
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / name
    path.write_text(json.dumps({
        "sessionId": name,
        "remoteMcpServersConfig": connectors,
        **({} if last_activity is None else {"lastActivityAt": last_activity}),
    }), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


class ClaudeAINameSourcesTest(unittest.TestCase):
    """The two name sources are unioned; neither is complete alone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir()
        # Default to a platform whose session dir is absent in the temp home.
        patcher = mock.patch.object(helpers.platform, "system", return_value="Darwin")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _extract(self):
        projects = []
        helpers.extract_claudeai_mcp_servers(self.claude_dir, projects)
        return projects

    def test_no_sources_reports_nothing(self):
        self.assertEqual(self._extract(), [])

    def test_auth_cache_only(self):
        _write_json(self.claude_dir / "mcp-needs-auth-cache.json", {
            "claude.ai Google Drive": {"timestamp": 1, "id": "mcpsrv_1"},
            "linear": {"timestamp": 2},
        })

        projects = self._extract()

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["scope"], "claudeai")
        self.assertEqual(projects[0]["path"], str(self.claude_dir))
        self.assertEqual(
            projects[0]["mcpServers"],
            [{"name": "claude.ai Google Drive", "scope": "claudeai"}],
        )

    def test_ever_connected_surfaces_authenticated_connectors(self):
        """The regression: an authenticated connector is absent from the auth
        cache but present in claudeAiMcpEverConnected."""
        _write_json(self.claude_dir / "mcp-needs-auth-cache.json", {
            "claude.ai Google Drive": {"timestamp": 1},
        })
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": [
                "claude.ai Google Drive",
                "claude.ai Gmail",
                "claude.ai Spotify",
            ],
            "mcpServers": {},
        })

        names = [s["name"] for s in self._extract()[0]["mcpServers"]]

        self.assertEqual(names, [
            "claude.ai Google Drive",
            "claude.ai Gmail",
            "claude.ai Spotify",
        ])

    def test_names_are_deduped_case_insensitively(self):
        _write_json(self.claude_dir / "mcp-needs-auth-cache.json", {
            "claude.ai Notion": {"timestamp": 1},
        })
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": ["claude.ai notion"],
        })

        names = [s["name"] for s in self._extract()[0]["mcpServers"]]

        self.assertEqual(names, ["claude.ai Notion"])

    def test_non_claudeai_entries_are_ignored(self):
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": ["linear", "claude.ai Gmail", 42, None],
        })

        names = [s["name"] for s in self._extract()[0]["mcpServers"]]

        self.assertEqual(names, ["claude.ai Gmail"])

    def test_malformed_files_do_not_raise(self):
        (self.claude_dir / "mcp-needs-auth-cache.json").write_text("{not json", encoding="utf-8")
        (self.home / ".claude.json").write_text("[]", encoding="utf-8")

        self.assertEqual(self._extract(), [])

    def test_ever_connected_wrong_type_is_ignored(self):
        _write_json(self.home / ".claude.json", {"claudeAiMcpEverConnected": "Gmail"})

        self.assertEqual(self._extract(), [])


class ClaudeAISessionToolsTest(unittest.TestCase):
    """Tool lists come from the desktop session files when they exist."""

    GMAIL_TOOLS = [
        {
            "name": "search_threads",
            "description": "Search Gmail threads.",
            "inputSchema": {"type": "object"},
            "annotations": {"title": "Search threads", "readOnlyHint": True},
            "vendorExtension": "dropped",
        },
        {"name": "create_draft", "description": "Draft a message."},
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir()
        self.sessions = (
            self.home / "Library" / "Application Support" / "Claude"
            / "local-agent-mode-sessions"
        )
        patcher = mock.patch.object(helpers.platform, "system", return_value="Darwin")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": ["claude.ai Gmail", "claude.ai Spotify"],
        })

    def _servers(self):
        projects = []
        helpers.extract_claudeai_mcp_servers(self.claude_dir, projects)
        return {s["name"]: s for s in projects[0]["mcpServers"]}

    def test_tools_attached_only_for_connectors_present_in_sessions(self):
        _write_session(self.sessions / "org", "local_a.json", [
            {"uuid": "u1", "name": "Gmail",
             "url": "https://gmailmcp.googleapis.com/mcp/v1",
             "tools": self.GMAIL_TOOLS},
        ], mtime=1_700_000_000)

        servers = self._servers()

        gmail_scan = servers["claude.ai Gmail"]["scan"]
        self.assertIsNone(gmail_scan["error"])
        self.assertEqual(gmail_scan["tool_count"], 2)
        self.assertEqual(gmail_scan["scanned_at"], "2023-11-14T22:13:20+00:00")
        self.assertEqual(
            [t["name"] for t in gmail_scan["tools"]],
            ["search_threads", "create_draft"],
        )
        # Spotify has no session record: reported by name, no scan block.
        self.assertNotIn("scan", servers["claude.ai Spotify"])
        self.assertEqual(servers["claude.ai Spotify"],
                         {"name": "claude.ai Spotify", "scope": "claudeai"})

    def test_session_url_is_never_reported(self):
        """A url would fingerprint the row as url:<host> instead of
        claudeai:<name>, splitting it from the hook-reported server."""
        _write_session(self.sessions / "org", "local_a.json", [
            {"uuid": "u1", "name": "Gmail",
             "url": "https://gmailmcp.googleapis.com/mcp/v1",
             "tools": self.GMAIL_TOOLS},
        ], mtime=1_700_000_000)

        gmail = self._servers()["claude.ai Gmail"]

        self.assertNotIn("url", gmail)
        self.assertNotIn("url", json.dumps(gmail))

    def test_tools_are_trimmed_to_known_fields(self):
        _write_session(self.sessions / "org", "local_a.json", [
            {"uuid": "u1", "name": "Gmail", "tools": self.GMAIL_TOOLS},
        ], mtime=1_700_000_000)

        first = self._servers()["claude.ai Gmail"]["scan"]["tools"][0]

        self.assertNotIn("vendorExtension", first)
        self.assertEqual(
            set(first),
            {"name", "title", "description", "inputSchema", "annotations"},
        )
        self.assertEqual(first["title"], "Search threads")

    def test_newest_session_wins(self):
        _write_session(self.sessions / "org", "local_old.json", [
            {"uuid": "u1", "name": "Gmail", "tools": [{"name": "stale_tool"}]},
        ], mtime=1_600_000_000)
        _write_session(self.sessions / "org", "local_new.json", [
            {"uuid": "u1", "name": "Gmail", "tools": [{"name": "current_tool"}]},
        ], mtime=1_700_000_000)

        tools = self._servers()["claude.ai Gmail"]["scan"]["tools"]

        self.assertEqual([t["name"] for t in tools], ["current_tool"])

    def test_claude_code_session_folder_is_read_too(self):
        code_sessions = (
            self.home / "Library" / "Application Support" / "Claude"
            / "claude-code-sessions"
        )
        _write_session(code_sessions / "s", "local_a.json", [
            {"uuid": "u1", "name": "Gmail", "tools": [{"name": "search_threads"}]},
        ], mtime=1_700_000_000)

        self.assertEqual(
            self._servers()["claude.ai Gmail"]["scan"]["tool_count"], 1)

    def test_malformed_session_file_is_skipped(self):
        self.sessions.mkdir(parents=True)
        (self.sessions / "local_bad.json").write_text("{not json", encoding="utf-8")
        _write_session(self.sessions, "local_good.json", [
            {"uuid": "u1", "name": "Gmail", "tools": [{"name": "search_threads"}]},
        ], mtime=1_700_000_000)

        self.assertEqual(
            self._servers()["claude.ai Gmail"]["scan"]["tool_count"], 1)

    def test_newest_session_wins_even_when_its_tool_list_is_empty(self):
        """The newest session is authoritative, full stop — no walking back
        through older files hunting for a populated list. Across a fleet some
        other device reports the same connector with its tools."""
        _write_session(self.sessions / "org", "local_old.json", [
            {"uuid": "u1", "name": "Gmail", "tools": self.GMAIL_TOOLS},
        ], mtime=1_600_000_000)
        _write_session(self.sessions / "org", "local_new.json", [
            {"uuid": "u1", "name": "Gmail", "tools": []},
        ], mtime=1_700_000_000)

        scan = self._servers()["claude.ai Gmail"]["scan"]

        self.assertEqual(scan["tool_count"], 0)
        self.assertIsNone(scan["tools"])
        self.assertEqual(scan["scanned_at"], "2023-11-14T22:13:20+00:00")

    def test_connector_absent_from_names_is_not_reported(self):
        """Session files only enrich; they never introduce a server on their own."""
        _write_session(self.sessions / "org", "local_a.json", [
            {"uuid": "u2", "name": "Asana", "tools": [{"name": "list_tasks"}]},
        ], mtime=1_700_000_000)

        self.assertNotIn("claude.ai Asana", self._servers())


class ClaudeAIRootScanTest(unittest.TestCase):
    """A root scan must not gate on ~/.claude/ existing — one of the two name
    sources is ~/.claude.json, which sits beside that directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.users = Path(self._tmp.name)
        self.home = self.users / "alice"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_user_with_config_but_no_claude_dir_is_scanned(self):
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": ["claude.ai Gmail"],
        })
        self.assertFalse((self.home / ".claude").exists())

        projects = []
        with mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[self.home]), \
             mock.patch.object(helpers.Path, "home", return_value=self.home), \
             mock.patch.object(helpers.platform, "system", return_value="Darwin"), \
             mock.patch("platform.system", return_value="Darwin"):
            helpers.extract_claudeai_mcp_servers_with_root_support(projects)

        self.assertEqual(
            [s["name"] for p in projects for s in p["mcpServers"]],
            ["claude.ai Gmail"],
        )


class ClaudeAIDesktopSurfaceMirrorTest(unittest.TestCase):
    """Cowork and Desktop report the account-level connectors, but only when the
    CLI and the desktop app are signed in to the same account."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for patcher in (
            mock.patch.object(utils, "_SENTRY_DSN", ""),
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.detector = AIToolsDetector()
        self.detector._claude_desktop_mcp_extractor = None
        self.detector.extract_all_cowork_skills = lambda: {"user_skills": []}

    def _sign_in(self, cli_account=None, desktop_account=None):
        cli_config = {"claudeAiMcpEverConnected": ["claude.ai Gmail", "claude.ai Linear"]}
        if cli_account:
            cli_config["oauthAccount"] = {"accountUuid": cli_account}
        _write_json(self.home / ".claude.json", cli_config)
        if desktop_account:
            _write_json(helpers._claude_app_support_dir(self.home) / "config.json",
                        {"lastKnownAccountUuid": desktop_account})

    def _connector_servers(self, tool_name):
        result = self.detector.process_single_tool({"name": tool_name})
        return [s for p in result.get("projects", []) for s in p["mcpServers"]
                if s.get("scope") == "claudeai"]

    def test_same_account_mirrors_to_cowork_and_desktop(self):
        self._sign_in("acct-1", "acct-1")
        expected = [
            {"name": "claude.ai Gmail", "scope": "claudeai"},
            {"name": "claude.ai Linear", "scope": "claudeai"},
        ]
        self.assertEqual(self._connector_servers("Claude Cowork"), expected)
        self.assertEqual(self._connector_servers("Claude Desktop"), expected)

    def test_different_accounts_are_not_mirrored(self):
        self._sign_in("acct-1", "acct-2")
        self.assertEqual(self._connector_servers("Claude Cowork"), [])
        self.assertEqual(self._connector_servers("Claude Desktop"), [])

    def test_missing_desktop_config_is_not_mirrored(self):
        self._sign_in("acct-1", None)
        self.assertEqual(self._connector_servers("Claude Desktop"), [])

    def test_missing_oauth_account_is_not_mirrored(self):
        self._sign_in(None, "acct-1")
        self.assertEqual(self._connector_servers("Claude Cowork"), [])

    def test_claude_code_output_is_unchanged_by_the_filter(self):
        self._sign_in("acct-1", "acct-2")
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(projects)
        self.assertEqual(
            projects,
            [{
                "path": str(self.home / ".claude"),
                "mcpServers": [
                    {"name": "claude.ai Gmail", "scope": "claudeai"},
                    {"name": "claude.ai Linear", "scope": "claudeai"},
                ],
                "scope": "claudeai",
            }],
        )


VISUALIZE = {"uuid": "u-v", "name": "visualize", "url": "https://sandbox.claudemcpcontent.com/mcp", "tools": []}


def _connector(name, url="https://mcp.example.com/mcp", tools=None):
    return {"uuid": f"uuid-{name}", "name": name, "url": url, "tools": tools, "instructions": "SECRET-INSTRUCTIONS"}


SLACK_TOOLS = [{"name": "send_message", "description": "Send.", "vendor": "dropped"}]


class ClaudeAIDesktopSessionConnectorsTest(unittest.TestCase):
    """Cowork and Desktop also learn connector names from the desktop app's own
    session files; the list only ever grows (add-only)."""

    def setUp(self):
        self.now = time.time()
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for patcher in (
            mock.patch.object(utils, "_SENTRY_DSN", ""),
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.detector = AIToolsDetector()
        self.detector._claude_desktop_mcp_extractor = None
        self.detector.extract_all_cowork_skills = lambda: {"user_skills": []}
        self.app_dir = helpers._claude_app_support_dir(self.home)

    def _session(self, connectors, age_days=1, account="acct-1", sub="local-agent-mode-sessions",
                 name="local_a.json", home=None):
        app_dir = helpers._claude_app_support_dir(home or self.home)
        return _write_session(app_dir / sub / account / "org-1", name, connectors,
                              self.now - age_days * 86400)

    def _desktop_account(self, account="acct-1"):
        _write_json(self.app_dir / "config.json", {"lastKnownAccountUuid": account})

    def _cli(self, names, account="acct-1"):
        _write_json(self.home / ".claude.json", {
            "claudeAiMcpEverConnected": names, "oauthAccount": {"accountUuid": account},
        })

    def _names(self, tool_name):
        result = self.detector.process_single_tool({"name": tool_name})
        return [s["name"] for p in result.get("projects", []) for s in p["mcpServers"]
                if s.get("scope") == "claudeai"]

    def _both_surfaces(self):
        return self._names("Claude Cowork"), self._names("Claude Desktop")

    def test_desktop_only_user_gets_names_on_cowork_and_desktop(self):
        self._desktop_account()
        self._session([_connector("Slack"), _connector("Atlassian"), VISUALIZE])
        expected = ["claude.ai Slack", "claude.ai Atlassian"]
        self.assertEqual(self._both_surfaces(), (expected, expected))

    def test_claude_code_row_is_unchanged(self):
        self._desktop_account()
        self._session([_connector("Slack")])
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(projects)
        self.assertEqual(projects, [])

    def test_cli_list_kept_in_order_and_only_missing_names_appended(self):
        self._cli(["claude.ai Gmail", "claude.ai Linear"])
        self._desktop_account()
        self._session([_connector("slack"), _connector("LINEAR"), _connector("Notion")])
        self.assertEqual(self._names("Claude Cowork"),
                         ["claude.ai Gmail", "claude.ai Linear", "claude.ai slack", "claude.ai Notion"])

    def test_cli_entry_present_in_both_is_left_untouched(self):
        self._cli(["claude.ai Slack"])
        self._desktop_account()
        self._session([_connector("Slack", tools=SLACK_TOOLS)])
        result = self.detector.process_single_tool({"name": "Claude Cowork"})
        servers = [s for p in result["projects"] for s in p["mcpServers"] if s.get("scope") == "claudeai"]
        self.assertEqual(len(servers), 1)
        self.assertIn("scan", servers[0])

    def test_cli_part_stays_gated_but_sessions_are_not(self):
        self._cli(["claude.ai Gmail"], account="acct-other")
        self._desktop_account()
        self._session([_connector("Slack")])
        self.assertEqual(self._both_surfaces(), (["claude.ai Slack"],) * 2)

    def test_visualize_dropped_only_on_its_own_host_and_claude_docs_kept(self):
        self._session([VISUALIZE, _connector("Claude Docs", url="https://docs.example.com"),
                       _connector("visualize", url="https://other.example.com/mcp"),
                       _connector("Visualize", url="http://user@SANDBOX.claudemcpcontent.com:443/x")])
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Claude Docs", "claude.ai visualize"])

    def test_other_account_ignored_when_desktop_account_known(self):
        self._desktop_account("acct-1")
        self._session([_connector("Mine")], account="acct-1", age_days=5)
        self._session([_connector("Theirs")], account="acct-2", age_days=1, name="local_b.json")
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Mine"])

    def test_any_account_used_when_config_missing_or_folder_absent(self):
        self._session([_connector("Theirs")], account="acct-2")
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Theirs"])
        self._desktop_account("acct-missing")
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Theirs"])

    def test_traversal_account_value_is_not_followed(self):
        self._desktop_account("..")
        self._session([_connector("Theirs")])
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Theirs"])

    def test_newest_file_across_both_session_folders_wins(self):
        self._desktop_account()
        self._session([_connector("Old")], age_days=3, sub="claude-code-sessions")
        self._session([_connector("New")], age_days=1)
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai New"])

    def test_newest_builtin_only_file_does_not_fall_back_to_older_file(self):
        self._desktop_account()
        self._session([_connector("Old")], age_days=3, sub="claude-code-sessions")
        self._session([VISUALIZE], age_days=1)
        self.assertEqual(self._names("Claude Desktop"), [])

    def test_file_older_than_30_days_is_ignored(self):
        self._session([_connector("Slack")], age_days=31)
        self.assertEqual(self._both_surfaces(), ([], []))

    def test_newest_file_with_empty_list_adds_nothing(self):
        self._session([_connector("Old")], age_days=3, name="local_old.json")
        self._session([], age_days=1, name="local_new.json")
        self.assertEqual(self._names("Claude Desktop"), [])

    def test_unusable_files_are_skipped_and_never_change_output(self):
        self._cli(["claude.ai Gmail"])
        self._desktop_account()
        org = self.app_dir / "local-agent-mode-sessions" / "acct-1" / "org-1"
        org.mkdir(parents=True)
        newest = self.now - 100
        for file_name, body in (("local_trunc.json", '{"remoteMcpServersConfig": [{"na'),
                                ("local_list.json", '[1, 2]'),
                                ("local_str.json", '{"remoteMcpServersConfig": "Slack"}')):
            (org / file_name).write_text(body, encoding="utf-8")
            os.utime(org / file_name, (newest, newest))
        self._session([1, "x", None, {"url": "https://a.example.com"}, {"name": "  "}, {"name": 7}], age_days=2,
                      name="local_odd.json")
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Gmail"])

    def test_oversize_file_is_skipped(self):
        self._session([_connector("Small")], age_days=3, name="local_small.json")
        self._session([_connector("Big", tools=[{"name": "x", "description": "d" * (6 * 1024 * 1024)}])],
                      age_days=1, name="local_big.json")
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Small"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFO support")
    def test_fifo_session_file_is_skipped_without_hanging(self):
        self._session([_connector("Real")], age_days=3, name="local_real.json")
        fifo = self.app_dir / "local-agent-mode-sessions" / "acct-1" / "org-1" / "local_fifo.json"
        os.mkfifo(fifo)
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Real"])
        self.assertIsNone(helpers._read_bounded_json(fifo, None))

    def test_oversized_or_unprintable_names_are_skipped(self):
        self._session([_connector("x" * 201), _connector("bad\nname"), _connector("Fine")])
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Fine"])

    def test_account_folder_match_ignores_case(self):
        self._desktop_account("ACCT-1")
        self._session([_connector("Mine")], account="acct-1", age_days=5)
        self._session([_connector("Theirs")], account="acct-2", age_days=1, name="local_b.json")
        self.assertEqual(self._names("Claude Desktop"), ["claude.ai Mine"])

    def test_added_entry_has_exactly_the_keys_of_a_cli_entry(self):
        self._cli(["claude.ai Slack"])
        self._desktop_account()
        self._session([_connector("Slack", tools=SLACK_TOOLS), _connector("Notion", tools=SLACK_TOOLS),
                       _connector("NoTools")])
        servers = {}
        for p in self.detector.process_single_tool({"name": "Claude Cowork"})["projects"]:
            servers.update({s["name"]: s for s in p["mcpServers"]})
        cli_entry = servers["claude.ai Slack"]
        added = servers["claude.ai Notion"]
        self.assertEqual(set(added), set(cli_entry))
        self.assertEqual(set(added["scan"]), set(cli_entry["scan"]))
        self.assertEqual(added["scan"]["tools"], [{"name": "send_message", "description": "Send."}])
        self.assertEqual(added["scan"]["tool_count"], 1)
        self.assertEqual(servers["claude.ai NoTools"], {"name": "claude.ai NoTools", "scope": "claudeai"})

    def test_nothing_but_names_and_tools_leaves_the_file(self):
        self._session([_connector("Slack", tools=SLACK_TOOLS)])
        rendered = json.dumps(self.detector.process_single_tool({"name": "Claude Cowork"}))
        for private in ("SECRET-INSTRUCTIONS", "uuid-Slack", "mcp.example.com", "vendor"):
            self.assertNotIn(private, rendered)

    def test_no_session_folders_gives_todays_output(self):
        self._cli(["claude.ai Gmail"])
        self._desktop_account()
        self.assertEqual(self._both_surfaces(), (["claude.ai Gmail"],) * 2)

    def test_windows_style_layout(self):
        with mock.patch("platform.system", return_value="Windows"):
            app_dir = helpers._claude_app_support_dir(self.home)
            self.assertEqual(app_dir, self.home / "AppData" / "Roaming" / "Claude")
            _write_session(app_dir / "claude-code-sessions" / "a" / "o", "local_w.json",
                           [_connector("Slack")], self.now - 60)
            self.assertEqual(helpers._newest_desktop_session_connectors(self.home)[1][0]["name"], "Slack")


class ClaudeAIDesktopSessionRootScanTest(unittest.TestCase):
    """On a root scan each home yields only its own session data."""

    def setUp(self):
        self.now = time.time()
        self._tmp = tempfile.TemporaryDirectory()
        self.users = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.alice, self.bob, self.root_home = (self.users / n for n in ("alice", "bob", "root"))
        for home in (self.alice, self.bob, self.root_home):
            home.mkdir()
        for patcher in (
            mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[self.alice, self.bob]),
            mock.patch.object(helpers.Path, "home", return_value=self.root_home),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _sessions(self, home, names, sub="claude-code-sessions"):
        return _write_session(helpers._claude_app_support_dir(home) / sub / "a" / "o", "local_x.json",
                              [_connector(n) for n in names], self.now - 60)

    def _scan(self):
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(projects, include_desktop_sessions=True)
        return {Path(p["path"]).parent.name: [s["name"] for s in p["mcpServers"]] for p in projects}

    def test_each_user_gets_only_their_own_connectors(self):
        self._sessions(self.alice, ["Slack"])
        self._sessions(self.bob, ["Notion"])
        self.assertEqual(self._scan(), {"alice": ["claude.ai Slack"], "bob": ["claude.ai Notion"]})

    def test_symlinked_sessions_folder_into_another_home_is_refused(self):
        self._sessions(self.bob, ["BobSecret"])
        app_dir = helpers._claude_app_support_dir(self.alice)
        app_dir.mkdir(parents=True)
        try:
            os.symlink(helpers._claude_app_support_dir(self.bob) / "claude-code-sessions",
                       app_dir / "claude-code-sessions")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual(self._scan(), {"bob": ["claude.ai BobSecret"]})

    def test_symlinked_session_file_into_another_home_is_refused(self):
        bob_file = self._sessions(self.bob, ["BobSecret"])
        org = helpers._claude_app_support_dir(self.alice) / "claude-code-sessions" / "a" / "o"
        org.mkdir(parents=True)
        try:
            os.symlink(bob_file, org / "local_x.json")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual(self._scan(), {"bob": ["claude.ai BobSecret"]})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFO support")
    def test_root_with_inherited_home_still_refuses_a_symlinked_file(self):
        bob_file = self._sessions(self.bob, ["BobSecret"])
        org = helpers._claude_app_support_dir(self.alice) / "claude-code-sessions" / "a" / "o"
        org.mkdir(parents=True)
        try:
            os.symlink(bob_file, org / "local_x.json")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        with mock.patch.object(helpers.os, "geteuid", return_value=0, create=True):
            found = helpers._newest_desktop_session_connectors(self.alice)
        self.assertIsNone(found)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFO support")
    def test_planted_fifo_in_another_home_does_not_hang_the_scan(self):
        org = helpers._claude_app_support_dir(self.alice) / "claude-code-sessions" / "a" / "o"
        org.mkdir(parents=True)
        os.mkfifo(org / "local_x.json")
        self._sessions(self.bob, ["Notion"])
        self.assertEqual(self._scan(), {"bob": ["claude.ai Notion"]})


STALE = ["claude.ai Gmail", "claude.ai Vercel", "claude.ai Linear"]


def _sign_in_cli(home: Path, names, account="acct-1", org="org-1"):
    oauth = {"accountUuid": account, **({"organizationUuid": org} if org else {})}
    _write_json(home / ".claude.json", {"claudeAiMcpEverConnected": names, "oauthAccount": oauth})


class ClaudeAISameOrgSessionConnectorsTest(unittest.TestCase):
    """When CLI and desktop share an account, the CLI org's recent session files decide the list."""

    setUp = ClaudeAIDesktopSessionConnectorsTest.setUp
    _session = ClaudeAIDesktopSessionConnectorsTest._session
    _desktop_account = ClaudeAIDesktopSessionConnectorsTest._desktop_account
    _names = ClaudeAIDesktopSessionConnectorsTest._names
    _both_surfaces = ClaudeAIDesktopSessionConnectorsTest._both_surfaces

    def _signed_in(self, org="org-1"):
        _sign_in_cli(self.home, STALE, org=org)
        self._desktop_account()

    def test_stale_cli_names_are_dropped(self):
        self._signed_in()
        self._session([_connector("Linear"), _connector("Notion"), VISUALIZE])
        expected = ["claude.ai Linear", "claude.ai Notion"]
        self.assertEqual(self._both_surfaces(), (expected, expected))

    def test_other_org_newest_file_is_ignored(self):
        self._signed_in()
        self._session([_connector("Linear"), _connector("Slack")], age_days=2)
        _write_session(self.app_dir / "claude-code-sessions" / "acct-1" / "org-2", "local_b.json",
                       [_connector("Claude Docs"), _connector("Extra")], self.now - 60)
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear", "claude.ai Slack"])

    def test_one_short_file_among_several_hides_nothing(self):
        self._signed_in()
        for index in range(3):
            self._session([_connector("Linear"), _connector("Slack")], age_days=2 + index, name=f"local_{index}.json")
        self._session([_connector("Slack")], age_days=1, name="local_new.json", sub="claude-code-sessions")
        self.assertEqual(sorted(self._names("Claude Desktop")), ["claude.ai Linear", "claude.ai Slack"])

    def test_listing_kept_for_22_days_and_dropped_after_31(self):
        self._signed_in()
        self._session([_connector("Linear")], age_days=1, name="local_new.json")
        self._session([_connector("Gmail")], age_days=22, name="local_22.json")
        self._session([_connector("Vercel")], age_days=31, name="local_31.json")
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear", "claude.ai Gmail"])

    def test_org_folder_match_ignores_case(self):
        self._signed_in(org="ORG-1")
        self._session([_connector("Linear")])
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear"])

    def test_only_the_newest_files_up_to_the_cap_are_read(self):
        self._signed_in()
        self._session([_connector("Linear")], age_days=1, name="local_1.json")
        self._session([_connector("Slack")], age_days=2, name="local_2.json")
        self._session([_connector("Notion")], age_days=3, name="local_3.json")
        with mock.patch.object(helpers, "_SESSION_MAX_FILES", 2):
            self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear", "claude.ai Slack"])

    def test_newest_listing_supplies_the_tools(self):
        self._signed_in()
        self._session([_connector("Slack", tools=SLACK_TOOLS)], age_days=1, name="local_new.json")
        self._session([_connector("Slack", tools=[{"name": "old"}])], age_days=3, name="local_old.json")
        servers = [s for p in self.detector.process_single_tool({"name": "Claude Cowork"})["projects"]
                   for s in p["mcpServers"]]
        self.assertEqual([s["scan"]["tools"] for s in servers], [[{"name": "send_message", "description": "Send."}]])

    def test_resaved_old_session_is_dated_by_its_last_activity(self):
        self._signed_in()
        self._session([_connector("Linear")], age_days=2)
        org_dir = self.app_dir / "claude-code-sessions" / "acct-1" / "org-1"
        _write_session(org_dir, "local_old.json", [_connector("Gmail")], self.now - 86400,
                       last_activity=(self.now - 40 * 86400) * 1000)
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear"])

    def test_latest_used_session_supplies_the_tools(self):
        self._signed_in()
        org_dir = self.app_dir / "claude-code-sessions" / "acct-1" / "org-1"
        _write_session(org_dir, "local_used.json", [_connector("Slack", tools=SLACK_TOOLS)], self.now - 3 * 86400,
                       last_activity=(self.now - 3 * 86400) * 1000)
        _write_session(org_dir, "local_resaved.json", [_connector("Slack", tools=[{"name": "old"}])],
                       self.now - 86400, last_activity=(self.now - 9 * 86400) * 1000)
        servers = [s for p in self.detector.process_single_tool({"name": "Claude Cowork"})["projects"]
                   for s in p["mcpServers"]]
        self.assertEqual([s["scan"]["tools"] for s in servers], [[{"name": "send_message", "description": "Send."}]])

    def test_resaved_old_sessions_do_not_use_up_the_cap(self):
        self._signed_in()
        org_dir = self.app_dir / "claude-code-sessions" / "acct-1" / "org-1"
        _write_session(org_dir, "local_used.json", [_connector("Linear")], self.now - 2 * 86400)
        for index in range(2):
            _write_session(org_dir, f"local_resaved_{index}.json", [_connector("Gmail")], self.now - 3600 - index,
                           last_activity=(self.now - 60 * 86400) * 1000)
        with mock.patch.object(helpers, "_SESSION_MAX_FILES", 2):
            self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear"])
        with mock.patch.object(helpers, "_SESSION_MAX_FILES_READ", 2):
            self.assertEqual(self._names("Claude Cowork"), STALE)

    def test_unusable_last_activity_falls_back_to_file_time(self):
        self._signed_in()
        org_dir = self.app_dir / "claude-code-sessions" / "acct-1" / "org-1"
        for index, junk in enumerate(("soon", True, -5, float("inf"))):
            _write_session(org_dir, f"local_{index}.json", [_connector(f"C{index}")], self.now - 86400 - index,
                           last_activity=junk)
        self.assertEqual(self._names("Claude Cowork"), [f"claude.ai C{index}" for index in range(4)])

    def test_every_fallback_matches_todays_output(self):
        def cli_desktop_mismatch():
            _sign_in_cli(self.home, STALE, account="acct-other")
            self._desktop_account()
            self._session([_connector("Slack")])

        def org_missing():
            self._signed_in(org=None)
            self._session([_connector("Slack")])

        def account_folder_absent():
            self._signed_in()
            self._session([_connector("Slack")], account="acct-old")

        cases = {
            "no cli": lambda: (self._desktop_account(), self._session([_connector("Slack")])),
            "account mismatch": cli_desktop_mismatch,
            "cli org missing": org_missing,
            "no same-org file": lambda: (self._signed_in(), _write_session(
                self.app_dir / "claude-code-sessions" / "acct-1" / "org-2", "local_b.json",
                [_connector("Slack")], self.now - 60)),
            "older than window": lambda: (self._signed_in(), self._session([_connector("Slack")], age_days=31)),
            "account folder absent": account_folder_absent,
            "empty list": lambda: (self._signed_in(), self._session([])),
            "built-ins only": lambda: (self._signed_in(), self._session([VISUALIZE])),
            "malformed file": lambda: (self._signed_in(), self._session(None), (
                self.app_dir / "local-agent-mode-sessions" / "acct-1" / "org-1" / "local_a.json"
            ).write_text('{"remoteMcpServersConfig": [{"na', encoding="utf-8")),
        }
        for label, build in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as fresh:
                self.home, self.app_dir = Path(fresh), helpers._claude_app_support_dir(Path(fresh))
                with mock.patch.object(Path, "home", return_value=self.home):
                    build()
                    rule_b = [self.detector.process_single_tool({"name": t})
                              for t in ("Claude Cowork", "Claude Desktop")]
                    with mock.patch.object(helpers, "replace_with_same_org_desktop_session_connectors",
                                           return_value=False):
                        today = [self.detector.process_single_tool({"name": t})
                                 for t in ("Claude Cowork", "Claude Desktop")]
                self.assertEqual(rule_b, today)
                self.assertTrue(all(row["projects"] for row in today))

    def test_large_cli_config_still_names_the_org(self):
        self._signed_in()
        config = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        _write_json(self.home / ".claude.json", {**config, "projects": "x" * (6 * 1024 * 1024)})
        self._session([_connector("Linear")])
        self.assertEqual(self._names("Claude Cowork"), ["claude.ai Linear"])

    def test_read_error_falls_back_to_todays_output(self):
        self._signed_in()
        self._session([_connector("Slack")])
        with mock.patch.object(helpers, "_same_org_desktop_session_servers", side_effect=OSError("boom")):
            self.assertEqual(self._names("Claude Cowork"),
                             ["claude.ai Gmail", "claude.ai Vercel", "claude.ai Linear", "claude.ai Slack"])

    def test_claude_code_row_is_unchanged(self):
        self._signed_in()
        self._session([_connector("Linear")])
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(projects)
        self.assertEqual([s["name"] for p in projects for s in p["mcpServers"]], STALE)

    def test_entry_has_exactly_the_keys_of_a_cli_entry_and_nothing_private(self):
        _sign_in_cli(self.home, ["claude.ai Slack"])
        cli_projects = []
        helpers.extract_claudeai_mcp_servers(self.home / ".claude", cli_projects)
        self._desktop_account()
        self._session([_connector("Slack", tools=SLACK_TOOLS), _connector("NoTools")])
        result = self.detector.process_single_tool({"name": "Claude Cowork"})
        servers = {s["name"]: s for p in result["projects"] for s in p["mcpServers"]}
        self.assertEqual(set(servers["claude.ai Slack"]), {"name", "scope", "scan"})
        self.assertEqual(servers["claude.ai NoTools"], {"name": "claude.ai NoTools", "scope": "claudeai"})
        self.assertEqual(set(servers["claude.ai Slack"]["scan"]),
                         {"scanned_at", "tools", "tool_count", "server_info", "error"})
        rendered = json.dumps(result)
        for private in ("SECRET-INSTRUCTIONS", "uuid-Slack", "mcp.example.com", "vendor", "acct-1", "org-1"):
            self.assertNotIn(private, rendered)


class ClaudeAISameOrgRootScanTest(unittest.TestCase):
    """A contained scan reads only the user's own CLI-org files."""

    setUp = ClaudeAIDesktopSessionRootScanTest.setUp

    def _scan(self):
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(
            projects, home_filter=helpers.claude_cli_and_desktop_share_account, include_desktop_sessions=True)
        return {Path(p["path"]).parent.name: [s["name"] for s in p["mcpServers"]] for p in projects}

    def _alice_signed_in(self, names=STALE):
        _sign_in_cli(self.alice, names, account="a", org="o")
        _write_json(helpers._claude_app_support_dir(self.alice) / "config.json", {"lastKnownAccountUuid": "a"})
        org = helpers._claude_app_support_dir(self.alice) / "claude-code-sessions" / "a" / "o"
        _write_session(org, "local_mine.json", [_connector("Mine")], self.now - 3600)
        return org

    def test_each_user_gets_their_own_org_list(self):
        self._alice_signed_in()
        self.assertEqual(self._scan(), {"alice": ["claude.ai Mine"]})

    def test_symlinked_session_file_into_another_home_is_refused(self):
        org = self._alice_signed_in()
        bob_file = _write_session(helpers._claude_app_support_dir(self.bob) / "x", "local_x.json",
                                  [_connector("BobSecret")], self.now - 60)
        try:
            os.symlink(bob_file, org / "local_x.json")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        self.assertEqual(self._scan(), {"alice": ["claude.ai Mine"]})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFO support")
    def test_planted_fifo_does_not_hang_the_scan(self):
        org = self._alice_signed_in()
        os.mkfifo(org / "local_fifo.json")
        self.assertEqual(self._scan(), {"alice": ["claude.ai Mine"]})

    def test_symlinked_cli_config_falls_back_to_todays_output(self):
        self._alice_signed_in()
        real_config = self.bob / "alice-config.json"
        (self.alice / ".claude.json").rename(real_config)
        try:
            os.symlink(real_config, self.alice / ".claude.json")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        with mock.patch.object(helpers, "replace_with_same_org_desktop_session_connectors", return_value=False):
            today = self._scan()
        self.assertEqual(self._scan(), today)


if __name__ == "__main__":
    unittest.main()
