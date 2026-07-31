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
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.mcp_extraction_helpers as helpers


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_session(session_dir: Path, name: str, connectors, mtime: float) -> Path:
    """Write one desktop session file carrying `remoteMcpServersConfig`."""
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / name
    path.write_text(json.dumps({
        "sessionId": name,
        "remoteMcpServersConfig": connectors,
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

    def test_connector_absent_from_names_is_not_reported(self):
        """Session files only enrich; they never introduce a server on their own."""
        _write_session(self.sessions / "org", "local_a.json", [
            {"uuid": "u2", "name": "Asana", "tools": [{"name": "list_tasks"}]},
        ], mtime=1_700_000_000)

        self.assertNotIn("claude.ai Asana", self._servers())


if __name__ == "__main__":
    unittest.main()
