"""
Tests for BreadcrumbCollector, report_to_sentry breadcrumb inclusion,
and send_run_summary_to_sentry.

Uses a real HTTP server on localhost for integration tests.
Only mocks: _SENTRY_DSN (to prevent real Sentry calls or to point at localhost).
"""

import json
import threading
import time
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    BreadcrumbCollector,
    _breadcrumbs,
    send_run_summary_to_sentry,
    report_to_sentry,
)


class _SentryMockHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures POST request bodies for assertion."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(json.loads(body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id": "test"}')

    def log_message(self, format, *args):
        pass  # suppress server logs


class TestBreadcrumbCollector(unittest.TestCase):
    """Unit tests for BreadcrumbCollector — uses fresh instances, not the singleton."""

    def test_add_breadcrumb(self):
        bc = BreadcrumbCollector()
        bc.add("http", "GET /api/v1/tools", level="info")

        payload = bc.get_breadcrumbs_payload()
        values = payload["values"]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["category"], "http")
        self.assertEqual(values[0]["message"], "GET /api/v1/tools")
        self.assertEqual(values[0]["level"], "info")
        self.assertIn("timestamp", values[0])

    def test_add_breadcrumb_with_data(self):
        bc = BreadcrumbCollector()
        bc.add("http", "POST /report", level="info", data={"status": 200})

        values = bc.get_breadcrumbs_payload()["values"]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["data"], {"status": 200})

    def test_max_breadcrumbs_cap(self):
        bc = BreadcrumbCollector()
        for i in range(110):
            bc.add("test", f"breadcrumb-{i}")

        values = bc.get_breadcrumbs_payload()["values"]
        self.assertEqual(len(values), 100)
        # Oldest entries should be dropped — first remaining should be #10
        self.assertEqual(values[0]["message"], "breadcrumb-10")
        self.assertEqual(values[-1]["message"], "breadcrumb-109")

    def test_record_error(self):
        bc = BreadcrumbCollector()
        err = RuntimeError("connection refused")
        bc.record_error("http", err, context={"url": "http://example.com"})

        # Should appear in breadcrumbs with level="error"
        values = bc.get_breadcrumbs_payload()["values"]
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["level"], "error")
        self.assertEqual(values[0]["message"], "connection refused")

        # Should appear in the errors list in the summary
        summary = bc.get_summary()
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["error_type"], "RuntimeError")
        self.assertEqual(summary["errors"][0]["error_message"], "connection refused")

    def test_max_errors_cap(self):
        bc = BreadcrumbCollector()
        for i in range(25):
            bc.record_error("test", ValueError(f"error-{i}"))

        summary = bc.get_summary()
        self.assertEqual(len(summary["errors"]), 20)
        # Oldest entries should be dropped — first remaining should be #5
        self.assertEqual(summary["errors"][0]["error_message"], "error-5")

    def test_phase_timing(self):
        bc = BreadcrumbCollector()
        bc.start_phase("detection")
        time.sleep(0.01)
        bc.end_phase("detection")

        summary = bc.get_summary()
        self.assertIn("detection", summary["phase_durations"])
        self.assertGreater(summary["phase_durations"]["detection"], 0)

    def test_set_stat(self):
        bc = BreadcrumbCollector()
        bc.set_stat("tools_found", 5)

        summary = bc.get_summary()
        self.assertEqual(summary["stats"]["tools_found"], 5)

    def test_increment_stat(self):
        bc = BreadcrumbCollector()
        bc.increment_stat("reports_sent")
        bc.increment_stat("reports_sent")

        summary = bc.get_summary()
        self.assertEqual(summary["stats"]["reports_sent"], 2)

    def test_increment_stat_with_amount(self):
        bc = BreadcrumbCollector()
        bc.increment_stat("bytes", 100)
        bc.increment_stat("bytes", 200)

        summary = bc.get_summary()
        self.assertEqual(summary["stats"]["bytes"], 300)

    def test_reset(self):
        bc = BreadcrumbCollector()
        bc.add("test", "crumb")
        bc.record_error("test", RuntimeError("err"))
        bc.set_stat("x", 1)
        bc.start_phase("p")
        bc.end_phase("p")

        bc.reset()

        self.assertEqual(bc.get_breadcrumbs_payload()["values"], [])
        summary = bc.get_summary()
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["stats"], {})
        self.assertEqual(summary["phase_durations"], {})

    def test_get_summary_total_duration(self):
        bc = BreadcrumbCollector()
        summary = bc.get_summary()
        self.assertIn("total_duration_seconds", summary)
        self.assertGreaterEqual(summary["total_duration_seconds"], 0)

    def test_never_raises_on_add(self):
        bc = BreadcrumbCollector()
        # None, empty strings, bad data — none should raise
        bc.add(None, None)
        bc.add("", "")
        bc.add("cat", "msg", data="not-a-dict")
        # Should not have raised — verify we got through
        self.assertGreaterEqual(len(bc.get_breadcrumbs_payload()["values"]), 0)

    def test_never_raises_on_record_error(self):
        bc = BreadcrumbCollector()
        # Non-Exception and None — should not raise
        bc.record_error("cat", "not-an-exception")
        bc.record_error("cat", None)
        # Should not have raised
        self.assertGreaterEqual(len(bc.get_summary()["errors"]), 0)

    def test_never_raises_on_phase(self):
        bc = BreadcrumbCollector()
        # end_phase without start_phase — should not raise
        bc.end_phase("never_started")
        summary = bc.get_summary()
        self.assertNotIn("never_started", summary["phase_durations"])


class TestReportToSentryIncludesBreadcrumbs(unittest.TestCase):
    """Verify that report_to_sentry includes breadcrumbs in the Sentry payload."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _SentryMockHandler)
        cls.server.requests = []
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
        _breadcrumbs.reset()

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def _get_dsn(self):
        """Helper to build a DSN pointing at the local server."""
        return f"http://testkey@127.0.0.1:{self.port}/123"

    def test_breadcrumbs_in_payload(self):
        _breadcrumbs.add("test", "breadcrumb-for-sentry", level="info")

        dsn = f"http://testkey@127.0.0.1:{self.port}/123"
        with patch.object(utils_mod, "_SENTRY_DSN", dsn):
            report_to_sentry(RuntimeError("test error"), {"phase": "test"})

        # Wait briefly for curl to complete
        deadline = time.time() + 5
        while time.time() < deadline and not self.server.requests:
            time.sleep(0.05)

        self.assertTrue(
            len(self.server.requests) > 0,
            "Expected at least one request to the mock Sentry server",
        )
        payload = self.server.requests[0]
        self.assertIn("breadcrumbs", payload)
        self.assertIn("values", payload["breadcrumbs"])
        messages = [v["message"] for v in payload["breadcrumbs"]["values"]]
        self.assertIn("breadcrumb-for-sentry", messages)


class TestSendRunSummary(unittest.TestCase):
    """Tests for send_run_summary_to_sentry."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _SentryMockHandler)
        cls.server.requests = []
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
        _breadcrumbs.reset()

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_summary_never_crashes_empty_dsn(self):
        # Should silently return without error
        send_run_summary_to_sentry("success")

    @patch.object(utils_mod, "_SENTRY_DSN", "invalid")
    def test_summary_never_crashes_bad_dsn(self):
        # Should silently handle the invalid DSN
        send_run_summary_to_sentry("crash", {"phase": "test"})

    def test_summary_sends_event(self):
        _breadcrumbs.add("test", "summary-breadcrumb", level="info")
        _breadcrumbs.set_stat("tools_found", 3)

        dsn = f"http://testkey@127.0.0.1:{self.port}/123"
        with patch.object(utils_mod, "_SENTRY_DSN", dsn):
            send_run_summary_to_sentry("success", {"device_id": "TEST123"})

        # Wait for curl to complete
        deadline = time.time() + 5
        while time.time() < deadline and not self.server.requests:
            time.sleep(0.05)

        self.assertTrue(
            len(self.server.requests) > 0,
            "Expected at least one request to the mock Sentry server",
        )
        payload = self.server.requests[0]

        # Message key present
        self.assertIn("message", payload)
        self.assertIn("formatted", payload["message"])
        self.assertIn("success", payload["message"]["formatted"])

        # Level is "info" for success
        self.assertEqual(payload["level"], "info")

        # Tags include outcome
        self.assertIn("tags", payload)
        self.assertEqual(payload["tags"]["outcome"], "success")

        # Extra includes summary data
        self.assertIn("extra", payload)
        self.assertIn("summary", payload["extra"])
        self.assertEqual(payload["extra"]["device_id"], "TEST123")

        # Breadcrumbs included
        self.assertIn("breadcrumbs", payload)
        messages = [v["message"] for v in payload["breadcrumbs"]["values"]]
        self.assertIn("summary-breadcrumb", messages)

    def test_summary_level_mapping(self):
        dsn = f"http://testkey@127.0.0.1:{self.port}/123"

        test_cases = [
            ("success", "info"),
            ("partial_failure", "warning"),
            ("crash", "error"),
        ]

        for outcome, expected_level in test_cases:
            with self.subTest(outcome=outcome):
                self.server.requests.clear()
                _breadcrumbs.reset()

                with patch.object(utils_mod, "_SENTRY_DSN", dsn):
                    send_run_summary_to_sentry(outcome)

                deadline = time.time() + 5
                while time.time() < deadline and not self.server.requests:
                    time.sleep(0.05)

                self.assertTrue(
                    len(self.server.requests) > 0,
                    f"Expected a request for outcome={outcome}",
                )
                payload = self.server.requests[0]
                self.assertEqual(
                    payload["level"],
                    expected_level,
                    f"Expected level '{expected_level}' for outcome '{outcome}', "
                    f"got '{payload['level']}'",
                )


if __name__ == "__main__":
    unittest.main()
