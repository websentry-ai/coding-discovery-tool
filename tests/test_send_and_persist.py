"""
Integration tests for send_report_to_backend() and queue persistence.

Uses a real HTTP server on localhost — curl hits it directly.
Only mocks: time.sleep (speed), QUEUE_FILE path (isolation), _SENTRY_DSN (no real Sentry).
"""

import json
import shutil
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
    send_scan_event,
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

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_large_payload_succeeds(self, _sleep):
        """Payloads exceeding ARG_MAX (~262KB) must not cause OSError."""
        large_report = {
            "home_user": "test",
            "device_id": "TEST123",
            "tools": [{"name": "x" * 300_000, "projects": []}],
        }
        success, retryable = send_report_to_backend(
            f"http://127.0.0.1:{self.port}",
            "test-key",
            large_report,
        )
        self.assertTrue(success)
        self.assertFalse(retryable)
        # Verify the server received the correct payload
        self.assertEqual(len(self.server.requests), 1)
        received = json.loads(self.server.requests[0]["body"])
        self.assertEqual(received["device_id"], "TEST123")


    @patch("time.sleep")
    @patch.object(utils_mod, "report_to_sentry")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_non_retryable_sentry_includes_response_body(self, mock_sentry, _sleep):
        self.server.default_code = 400
        self.server.response_body = b'{"skills": ["Unknown field"]}'

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertFalse(success)
        self.assertFalse(retryable)
        mock_sentry.assert_called()
        # Check the context dict (second positional arg) contains response_body
        call_args = mock_sentry.call_args
        ctx = call_args[0][1]
        self.assertIn("response_body", ctx)
        self.assertEqual(ctx["response_body"], '{"skills": ["Unknown field"]}')
        # Check the RuntimeError message includes the response body snippet
        exc = call_args[0][0]
        self.assertIn('{"skills": ["Unknown field"]}', str(exc))

    @patch("time.sleep")
    @patch.object(utils_mod, "report_to_sentry")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_retryable_exhaustion_sentry_includes_response_body(self, mock_sentry, _sleep):
        self.server.default_code = 500
        self.server.response_body = b'{"error": "internal"}'

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertFalse(success)
        self.assertTrue(retryable)
        mock_sentry.assert_called()
        call_args = mock_sentry.call_args
        ctx = call_args[0][1]
        self.assertIn("response_body", ctx)
        self.assertEqual(ctx["response_body"], '{"error": "internal"}')

    @patch("time.sleep")
    @patch.object(utils_mod, "report_to_sentry")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_sentry_context_includes_payload_metadata(self, mock_sentry, _sleep):
        self.server.default_code = 400
        self.server.response_body = b"{}"

        success, retryable = send_report_to_backend(
            self.base_url, "test-key", self.report
        )

        self.assertFalse(success)
        self.assertFalse(retryable)
        mock_sentry.assert_called()
        call_args = mock_sentry.call_args
        ctx = call_args[0][1]
        self.assertIn("payload_size_bytes", ctx)
        self.assertIsInstance(ctx["payload_size_bytes"], int)
        self.assertGreater(ctx["payload_size_bytes"], 0)
        self.assertIn("payload_keys", ctx)
        # report keys are device_id, home_user, tools — sorted
        self.assertEqual(ctx["payload_keys"], "device_id,home_user,tools")


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

        # Queue file should exist
        self.assertTrue(self._queue_path.exists())

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


class TestScanEvents(unittest.TestCase):
    """Tests for scan lifecycle event tracking."""

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

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_in_progress_event(self, _sleep):
        """Test sending scan in_progress event."""
        success, retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEVICE123",
            "run-uuid-1234",
            "in_progress",
            app_name="JumpCloud"
        )

        self.assertTrue(success)
        self.assertFalse(retryable)
        self.assertEqual(len(self.server.requests), 1)

        # Verify payload structure
        payload = json.loads(self.server.requests[0]["body"])
        self.assertEqual(payload["device_id"], "DEVICE123")
        self.assertEqual(payload["run_id"], "run-uuid-1234")
        self.assertEqual(payload["scan_event"], "in_progress")
        self.assertEqual(payload["app_name"], "JumpCloud")
        self.assertNotIn("home_user", payload)
        self.assertNotIn("scan_error", payload)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_completed_event(self, _sleep):
        """Test sending scan completed event."""
        success, retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEVICE123",
            "run-uuid-1234",
            "completed"
        )

        self.assertTrue(success)
        self.assertFalse(retryable)

        payload = json.loads(self.server.requests[0]["body"])
        self.assertEqual(payload["scan_event"], "completed")

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_failed_with_user_error(self, _sleep):
        """Test sending scan failed event with user-specific error."""
        scan_error = {
            "error_type": "PermissionError",
            "message": "Access denied to /Users/alice/.cursor",
            "timestamp": "2024-01-01T00:00:00Z"
        }

        success, retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEVICE123",
            "run-uuid-1234",
            "failed",
            home_user="alice",
            scan_error=scan_error
        )

        self.assertTrue(success)
        self.assertFalse(retryable)

        payload = json.loads(self.server.requests[0]["body"])
        self.assertEqual(payload["scan_event"], "failed")
        self.assertEqual(payload["home_user"], "alice")
        self.assertEqual(payload["scan_error"]["error_type"], "PermissionError")

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_failed_device_level_error(self, _sleep):
        """Test sending scan failed event for device-level error (no home_user)."""
        scan_error = {
            "error_type": "RuntimeError",
            "message": "Script crashed",
            "timestamp": "2024-01-01T00:00:00Z"
        }

        success, retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEVICE123",
            "run-uuid-1234",
            "failed",
            scan_error=scan_error
        )

        self.assertTrue(success)
        self.assertFalse(retryable)

        payload = json.loads(self.server.requests[0]["body"])
        self.assertEqual(payload["scan_event"], "failed")
        self.assertNotIn("home_user", payload)  # No user context for device-level errors
        self.assertEqual(payload["scan_error"]["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
