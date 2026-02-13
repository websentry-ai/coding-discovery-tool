"""
Integration tests for send_report_to_backend() and queue persistence.

Uses a real HTTP server on localhost — no mocking of urllib.
Only mocks: time.sleep (speed), QUEUE_FILE path (isolation), _SENTRY_DSN (no real Sentry).
"""

import json
import os
import platform
import shutil
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    send_report_to_backend,
    save_failed_reports,
    load_pending_reports,
    MAX_QUEUE_SIZE,
)


class _MockHandler(BaseHTTPRequestHandler):
    """HTTP handler that returns configurable status codes per request."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )

        # Pop the next configured code, or fall back to default
        if self.server.response_codes:
            code = self.server.response_codes.pop(0)
        else:
            code = self.server.default_code

        response_body = self.server.response_body or b""

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        pass  # suppress server logs


class TestSendReport(unittest.TestCase):
    """Integration tests for the HTTP send + retry logic."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MockHandler)
        cls.server.requests = []
        cls.server.response_codes = []
        cls.server.default_code = 200
        cls.server.response_body = b""
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
        self.server.response_codes.clear()
        self.server.default_code = 200
        self.server.response_body = b""
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.report = {"home_user": "alice", "device_id": "TEST123", "tools": []}

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_successful_send(self, _sleep):
        self.server.default_code = 200
        original = json.loads(json.dumps(self.report))

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report, app_name="TestApp"
        )

        self.assertTrue(success)
        self.assertFalse(retryable)
        # Original report must NOT be mutated (app_name only on payload copy)
        self.assertEqual(self.report, original)
        self.assertNotIn("app_name", self.report)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_retry_then_success(self, _sleep):
        self.server.response_codes = [500, 500]
        self.server.default_code = 200

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertTrue(success)
        self.assertFalse(retryable)
        self.assertEqual(len(self.server.requests), 3)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_retry_exhaustion_queues(self, _sleep):
        self.server.default_code = 500

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertFalse(success)
        self.assertTrue(retryable)
        self.assertEqual(len(self.server.requests), 3)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_non_retryable_no_retry(self, _sleep):
        self.server.default_code = 401

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertFalse(success)
        self.assertFalse(retryable)
        self.assertEqual(len(self.server.requests), 1)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_non_retryable_codes(self, _sleep):
        for code in (400, 401, 403, 404, 405, 422):
            with self.subTest(code=code):
                self.server.requests.clear()
                self.server.response_codes.clear()
                self.server.default_code = code
                self.server.response_body = b""

                success, retryable = send_report_to_backend(
                    self.base_url, "test-key", self.report
                )

                self.assertFalse(success, f"Expected failure for {code}")
                self.assertFalse(retryable, f"Expected non-retryable for {code}")
                self.assertEqual(
                    len(self.server.requests), 1, f"Expected 1 request for {code}"
                )

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_cloudflare_403_retries(self, _sleep):
        self.server.default_code = 403
        self.server.response_body = b"<html>Error 1010: Access denied</html>"

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        # Cloudflare 403 with "1010" in body is treated as transient -> retries
        self.assertFalse(success)
        self.assertTrue(retryable)
        self.assertEqual(len(self.server.requests), 3)


class TestPersistence(unittest.TestCase):
    """Integration tests for queue persistence lifecycle."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._queue_path = Path(self._tmp_dir) / "queue.json"
        self._orig_queue_file = utils_mod.QUEUE_FILE
        utils_mod.QUEUE_FILE = self._queue_path

    def tearDown(self):
        utils_mod.QUEUE_FILE = self._orig_queue_file
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_persist_and_drain_lifecycle(self):
        reports = [
            {"home_user": "alice", "device_id": "D1", "tools": []},
            {"home_user": "bob", "device_id": "D2", "tools": []},
        ]

        save_failed_reports(reports)

        # Queue file should exist with correct permissions (Unix only)
        self.assertTrue(self._queue_path.exists())
        if platform.system() != "Windows":
            file_mode = oct(self._queue_path.stat().st_mode & 0o777)
            self.assertEqual(file_mode, oct(0o600))

        # Load and verify round-trip
        loaded = load_pending_reports()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["home_user"], "alice")
        self.assertEqual(loaded[1]["home_user"], "bob")

    def test_expired_reports_discarded(self):
        old_time = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        envelopes = [
            {"report": {"home_user": "stale", "tools": []}, "queued_at": old_time}
        ]
        self._queue_path.write_text(json.dumps(envelopes))

        loaded = load_pending_reports()
        self.assertEqual(loaded, [])

    def test_queue_caps_at_100(self):
        reports = [{"id": i} for i in range(120)]
        save_failed_reports(reports)

        raw = json.loads(self._queue_path.read_text())
        self.assertLessEqual(len(raw), MAX_QUEUE_SIZE)

    def test_queue_merges_existing(self):
        save_failed_reports([{"id": i} for i in range(5)])
        save_failed_reports([{"id": i} for i in range(5, 8)])

        loaded = load_pending_reports()
        self.assertEqual(len(loaded), 8)


if __name__ == "__main__":
    unittest.main()
