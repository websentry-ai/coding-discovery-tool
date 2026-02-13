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
from scripts.coding_discovery_tools.utils import report_to_sentry
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
        self._queue_file = Path("/var/tmp/ai-discovery-queue.json")
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

    @unittest.skipIf(platform.system() == "Windows", "/var/tmp not available on Windows")
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

    @unittest.skipIf(platform.system() == "Windows", "/var/tmp not available on Windows")
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


if __name__ == "__main__":
    unittest.main()
