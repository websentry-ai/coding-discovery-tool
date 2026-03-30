"""
Integration tests for the full discovery + reporting flow.

Tests the outermost entry points: AIToolsDetector, main() CLI, report_to_sentry,
settings transformation, and project filtering.

Only mocks external environments: HTTP backend (real server on localhost),
QUEUE_FILE path (tempfile), _SENTRY_DSN (prevent real calls), time.sleep.
Tool detection runs un-mocked on whatever OS is available.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    report_to_sentry,
    _parse_sentry_dsn,
    _extract_frames,
)
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.settings_transformers import (
    transform_settings_to_backend_format,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _CLIMockHandler(BaseHTTPRequestHandler):
    """Simple handler that records requests and returns configurable status."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(json.loads(body))

        code = self.server.default_code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass


class TestDetector(unittest.TestCase):
    """Integration tests for AIToolsDetector on the current OS."""

    def test_detector_initializes(self):
        detector = AIToolsDetector()
        device_id = detector.get_device_id()
        self.assertIsInstance(device_id, str)
        self.assertTrue(len(device_id) > 0)

    def test_detect_and_report_flow(self):
        detector = AIToolsDetector()
        device_id = detector.get_device_id()
        tools = detector.detect_all_tools()
        # tools may be empty if no AI tools installed — that's fine
        self.assertIsInstance(tools, list)

        for tool in tools:
            processed = detector.process_single_tool(tool)
            report = detector.generate_single_tool_report(
                processed, device_id, "testuser"
            )
            # Assert required top-level keys
            self.assertIn("home_user", report)
            self.assertIn("system_user", report)
            self.assertIn("device_id", report)
            self.assertIn("tools", report)
            self.assertEqual(len(report["tools"]), 1)

            reported_tool = report["tools"][0]
            self.assertIn("name", reported_tool)
            self.assertIn("projects", reported_tool)


class TestMainCLI(unittest.TestCase):
    """Integration tests that invoke main() via subprocess."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _CLIMockHandler)
        cls.server.requests = []
        cls.server.default_code = 200
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.requests.clear()
        self.server.default_code = 200
        self._queue_file = utils_mod.QUEUE_FILE
        # Ensure clean state
        if self._queue_file.exists():
            self._queue_file.unlink()

    def tearDown(self):
        if self._queue_file.exists():
            self._queue_file.unlink(missing_ok=True)

    def _run_cli(self, extra_env=None, timeout=600):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                sys.executable,
                "scripts/coding_discovery_tools/ai_tools_discovery.py",
                "--api-key",
                "test-key-000000",
                "--domain",
                f"http://127.0.0.1:{self.port}",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def test_main_cli_happy_path(self):
        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")


    def test_main_cli_with_queue_drain(self):
        # Pre-populate queue with a distinctive report
        queued_report = {
            "home_user": "queued-user",
            "device_id": "QUEUED-DEVICE",
            "tools": [{"name": "FakeTool", "version": "0.0.0", "projects": []}],
        }
        from datetime import datetime, timezone

        envelope = [
            {
                "report": queued_report,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        self._queue_file.write_text(json.dumps(envelope))

        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Mock server should have received the queued report
        queued_bodies = [
            r for r in self.server.requests if r.get("device_id") == "QUEUED-DEVICE"
        ]
        self.assertGreaterEqual(len(queued_bodies), 1, "Queued report was not drained")


    def test_main_cli_persists_failures(self):
        self.server.default_code = 500

        # Pre-populate queue so there's something to fail
        from datetime import datetime, timezone

        envelope = [
            {
                "report": {"home_user": "fail-user", "device_id": "FAIL", "tools": []},
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        self._queue_file.write_text(json.dumps(envelope))

        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Queue file should be re-created with failed reports
        self.assertTrue(
            self._queue_file.exists(),
            "Queue file should exist after failures",
        )
        data = json.loads(self._queue_file.read_text())
        self.assertGreaterEqual(len(data), 1)


class TestSentryNeverCrashes(unittest.TestCase):
    """report_to_sentry must never raise, regardless of input."""

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_empty_dsn(self):
        report_to_sentry(RuntimeError("boom"))

    @patch.object(utils_mod, "_SENTRY_DSN", "not-a-valid-dsn")
    def test_with_bad_dsn(self):
        report_to_sentry(ValueError("bad"))

    @patch.object(utils_mod, "_SENTRY_DSN", "https://key@localhost:1/0")
    def test_with_unreachable_host(self):
        report_to_sentry(IOError("net"), context={"phase": "test"})

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_no_traceback(self):
        exc = RuntimeError("no tb")
        exc.__traceback__ = None
        report_to_sentry(exc)

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_none_context(self):
        report_to_sentry(RuntimeError("x"), context=None, level="warning")


class TestParseSentryDsn(unittest.TestCase):
    """_parse_sentry_dsn correctly parses valid DSNs and rejects invalid ones."""

    def test_valid_dsn(self):
        result = _parse_sentry_dsn("https://abc123@o123.ingest.us.sentry.io/456")
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "abc123")
        self.assertEqual(result["host"], "o123.ingest.us.sentry.io")
        self.assertEqual(result["project_id"], "456")
        self.assertEqual(result["store_url"], "https://o123.ingest.us.sentry.io/api/456/store/")

    def test_empty_string(self):
        self.assertIsNone(_parse_sentry_dsn(""))

    def test_malformed_dsn(self):
        self.assertIsNone(_parse_sentry_dsn("not-a-valid-dsn"))

    def test_missing_key(self):
        self.assertIsNone(_parse_sentry_dsn("https://sentry.io/123"))


class TestExtractFrames(unittest.TestCase):
    """_extract_frames returns real frames from raised exceptions and empty list otherwise."""

    def test_exception_with_traceback(self):
        try:
            raise RuntimeError("test error")
        except RuntimeError as exc:
            frames = _extract_frames(exc)
        self.assertGreater(len(frames), 0)
        self.assertIn("filename", frames[0])
        self.assertIn("lineno", frames[0])
        self.assertIn("function", frames[0])

    def test_exception_without_traceback(self):
        exc = RuntimeError("no traceback")
        exc.__traceback__ = None
        self.assertEqual(_extract_frames(exc), [])

    def test_manually_constructed_exception_has_no_frames(self):
        exc = RuntimeError("manual")
        self.assertEqual(_extract_frames(exc), [])


class TestSentryDebugLogging(unittest.TestCase):
    """Sentry helpers log debug messages for missing DSN and curl failures."""

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_logs_debug_when_dsn_missing(self):
        with self.assertLogs("scripts.coding_discovery_tools.utils", level="DEBUG") as cm:
            report_to_sentry(RuntimeError("test"))
        self.assertTrue(any("no valid DSN configured" in msg for msg in cm.output))

    @patch.object(utils_mod, "_SENTRY_DSN", "https://key@localhost:1/0")
    @patch("subprocess.run", side_effect=OSError("connection refused"))
    def test_logs_debug_when_sentry_curl_fails(self, mock_run):
        with self.assertLogs("scripts.coding_discovery_tools.utils", level="DEBUG") as cm:
            report_to_sentry(RuntimeError("test"))
        self.assertTrue(any("Sentry reporting failed" in msg for msg in cm.output))


class TestSettingsTransformPrecedence(unittest.TestCase):
    """Settings transformation picks highest precedence and maps fields correctly."""

    def test_managed_wins_over_user(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/path/user.json",
                "permissions": {"defaultMode": "ask", "allow": ["Read"]},
                "sandbox": {"enabled": False},
            },
            {
                "scope": "managed",
                "settings_path": "/path/managed.json",
                "permissions": {
                    "defaultMode": "deny",
                    "allow": ["Bash"],
                    "deny": ["Write"],
                },
                "sandbox": {"enabled": True},
            },
        ]

        result = transform_settings_to_backend_format(settings)

        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "managed")
        self.assertEqual(result["permission_mode"], "deny")
        self.assertEqual(result["allow_rules"], ["Bash"])
        self.assertEqual(result["deny_rules"], ["Write"])
        self.assertTrue(result["sandbox_enabled"])

    def test_field_mapping(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/s.json",
                "permissions": {
                    "defaultMode": "default",
                    "allow": ["a"],
                    "deny": ["d"],
                    "ask": ["q"],
                    "additionalDirectories": ["/tmp"],
                },
                "sandbox": {"enabled": True},
            }
        ]

        result = transform_settings_to_backend_format(settings)

        self.assertEqual(result["permission_mode"], "default")
        self.assertEqual(result["allow_rules"], ["a"])
        self.assertEqual(result["deny_rules"], ["d"])
        self.assertEqual(result["ask_rules"], ["q"])
        self.assertEqual(result["additional_directories"], ["/tmp"])
        self.assertTrue(result["sandbox_enabled"])

    def test_empty_settings_returns_none(self):
        self.assertIsNone(transform_settings_to_backend_format([]))


class TestFilterProjectsByUser(unittest.TestCase):
    """filter_tool_projects_by_user keeps only paths under the given user home."""

    def test_filters_by_user_home(self):
        detector = AIToolsDetector()
        # Use OS-agnostic paths so str(Path(...)) matches the project path strings
        alice_home = str(Path(tempfile.gettempdir()) / "alice")
        bob_home = str(Path(tempfile.gettempdir()) / "bob")
        tool = {
            "name": "TestTool",
            "version": "1.0",
            "install_path": str(Path(tempfile.gettempdir()) / "bin" / "test"),
            "projects": [
                {"path": os.path.join(alice_home, "project-a"), "rules": [], "mcpServers": []},
                {"path": os.path.join(bob_home, "project-b"), "rules": [], "mcpServers": []},
                {"path": os.path.join(alice_home, "project-c"), "rules": [], "mcpServers": []},
            ],
        }

        filtered = detector.filter_tool_projects_by_user(tool, Path(alice_home))

        self.assertEqual(len(filtered["projects"]), 2)
        paths = [p["path"] for p in filtered["projects"]]
        self.assertIn(os.path.join(alice_home, "project-a"), paths)
        self.assertIn(os.path.join(alice_home, "project-c"), paths)
        self.assertNotIn(os.path.join(bob_home, "project-b"), paths)

    def test_no_matching_projects(self):
        detector = AIToolsDetector()
        alice_home = str(Path(tempfile.gettempdir()) / "alice")
        bob_home = str(Path(tempfile.gettempdir()) / "bob")
        tool = {
            "name": "TestTool",
            "projects": [
                {"path": os.path.join(bob_home, "proj"), "rules": []},
            ],
        }

        filtered = detector.filter_tool_projects_by_user(tool, Path(alice_home))
        self.assertEqual(filtered["projects"], [])


    def test_prefix_collision_excludes_similar_username(self):
        detector = AIToolsDetector()
        # Use OS-agnostic paths via tempdir so separators match on Windows
        base = str(Path(tempfile.gettempdir()) / "Users")
        gowshik_home = os.path.join(base, "gowshik")
        gowshik_2_home = os.path.join(base, "gowshik_2")

        # Build all paths with os.path.join for cross-platform compatibility
        gow_proj1 = os.path.join(gowshik_home, "unbound", "unbound-fe")
        gow_proj2 = os.path.join(gowshik_home, "personal", "blog")
        gow2_proj1 = os.path.join(gowshik_2_home, "unbound", "unbound-fe")
        gow2_settings = os.path.join(gowshik_2_home, ".cursor", "settings.json")

        tool = {
            "name": "TestTool",
            "version": "1.0",
            "projects": [
                {
                    "path": gow_proj1,
                    "mcpServers": [{"name": "sentry"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow_proj1, ".cursorrules"),
                            "content": "be concise",
                        }
                    ],
                    "skills": [
                        {
                            "name": "deploy",
                            "file_path": os.path.join(gow_proj1, ".claude", "skills", "deploy", "skill.md"),
                        }
                    ],
                },
                {
                    "path": gow_proj2,
                    "mcpServers": [{"name": "postgres"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow_proj2, ".cursorrules"),
                            "content": "use markdown",
                        }
                    ],
                    "skills": [
                        {
                            "name": "publish",
                            "file_path": os.path.join(gow_proj2, ".claude", "skills", "publish", "skill.md"),
                        }
                    ],
                },
                {
                    "path": gow2_proj1,
                    "mcpServers": [{"name": "linear"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow2_proj1, ".cursorrules"),
                            "content": "use TypeScript",
                        }
                    ],
                    "skills": [
                        {
                            "name": "review",
                            "file_path": os.path.join(gow2_proj1, ".claude", "skills", "review", "skill.md"),
                        }
                    ],
                },
            ],
            "permissions": {
                "settings_path": gow2_settings,
                "settings_source": "user",
            },
        }

        # --- Filter for gowshik ---
        filtered_gowshik = detector.filter_tool_projects_by_user(tool, Path(gowshik_home))

        # Only gowshik's 2 projects remain
        self.assertEqual(len(filtered_gowshik["projects"]), 2)

        # gowshik_2's project (with linear MCP server) is NOT included
        filtered_paths = [p["path"] for p in filtered_gowshik["projects"]]
        self.assertNotIn(gow2_proj1, filtered_paths)

        # MCP servers in kept projects are sentry and postgres (not linear)
        mcp_names = [
            s["name"]
            for p in filtered_gowshik["projects"]
            for s in p.get("mcpServers", [])
        ]
        self.assertIn("sentry", mcp_names)
        self.assertIn("postgres", mcp_names)
        self.assertNotIn("linear", mcp_names)

        # Rules in kept projects are gowshik's rules (not gowshik_2's)
        rule_contents = [
            r["content"]
            for p in filtered_gowshik["projects"]
            for r in p.get("rules", [])
        ]
        self.assertIn("be concise", rule_contents)
        self.assertIn("use markdown", rule_contents)
        self.assertNotIn("use TypeScript", rule_contents)

        # Skills in kept projects are gowshik's skills (not gowshik_2's)
        skill_names = [
            s["name"]
            for p in filtered_gowshik["projects"]
            for s in p.get("skills", [])
        ]
        self.assertIn("deploy", skill_names)
        self.assertIn("publish", skill_names)
        self.assertNotIn("review", skill_names)

        # Permissions block is removed (settings_path is under gowshik_2's home)
        self.assertNotIn("permissions", filtered_gowshik)

        # --- Filter for gowshik_2 ---
        filtered_gowshik_2 = detector.filter_tool_projects_by_user(tool, Path(gowshik_2_home))

        # Only gowshik_2's 1 project remains
        self.assertEqual(len(filtered_gowshik_2["projects"]), 1)

        # gowshik's projects are NOT included
        filtered_paths_2 = [p["path"] for p in filtered_gowshik_2["projects"]]
        self.assertNotIn(gow_proj1, filtered_paths_2)
        self.assertNotIn(gow_proj2, filtered_paths_2)

        # MCP server is linear (not sentry or postgres)
        mcp_names_2 = [
            s["name"]
            for p in filtered_gowshik_2["projects"]
            for s in p.get("mcpServers", [])
        ]
        self.assertIn("linear", mcp_names_2)
        self.assertNotIn("sentry", mcp_names_2)
        self.assertNotIn("postgres", mcp_names_2)

        # Permissions block is kept (settings_path is under gowshik_2's home)
        self.assertIn("permissions", filtered_gowshik_2)


if __name__ == "__main__":
    unittest.main()
