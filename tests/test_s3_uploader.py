"""
Tests for the 3-step S3 upload flow in s3_uploader.py.

Pattern matches tests/test_send_and_persist.py: a real HTTP server runs on
localhost so the curl subprocess hits something real. We additionally route
the S3 PUT through the same localhost server so we can observe / fail it.
"""
import json
import threading
import unittest
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

import copy

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.s3_uploader import (
    compute_payload_hash,
    should_use_s3,
    try_s3_upload,
    _strip_ephemeral,
)
from scripts.coding_discovery_tools.utils import send_report_to_backend


# ────────────────────────────────────────────────────────────────────
# A configurable HTTP server that handles BOTH the backend endpoints
# AND the S3 PUT — based on URL path. This lets us simulate every
# stage of the 3-step flow in one process.
# ────────────────────────────────────────────────────────────────────
class _DualHandler(BaseHTTPRequestHandler):
    def _record_and_respond(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(
            {"method": self.command, "path": self.path, "body": body}
        )

        # Pick configured response for this path or default
        per_path = self.server.path_responses.get(self.path)
        if per_path:
            code, response_body = per_path.pop(0)
        else:
            code = self.server.default_code
            response_body = self.server.default_body

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    def do_POST(self):
        self._record_and_respond()

    def do_PUT(self):
        self._record_and_respond()

    def log_message(self, format, *args):
        pass


class _ServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _DualHandler)
        cls.server.requests = []
        cls.server.default_code = 200
        cls.server.default_body = b""
        cls.server.path_responses = {}
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
        self.server.path_responses.clear()
        self.server.default_code = 200
        self.server.default_body = b""
        self.base_url = f"http://127.0.0.1:{self.port}"

    def _set_path_response(self, path, code, body):
        self.server.path_responses.setdefault(path, []).append((code, body))


# ────────────────────────────────────────────────────────────────────
# Direct tests of try_s3_upload
# ────────────────────────────────────────────────────────────────────
class TestS3UploadFlow(_ServerMixin, unittest.TestCase):
    def _payload(self):
        return {
            "device_id": "DEV-1",
            "system_user": "alice",
            "home_user": "alice",
            "run_id": str(uuid.uuid4()),
            "tools": [{"name": "Cursor", "projects": []}],
        }

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_happy_path_three_steps(self):
        """Step 1 → 200 with URL, Step 2 → 200, Step 3 → 202."""
        upload_url = f"{self.base_url}/s3-bucket/object-key.json"
        upload_response = json.dumps({
            "upload_url": upload_url,
            "object_key": "org/1/run/abc.json",
            "expires_in": 300,
        }).encode()

        self._set_path_response("/api/v1/ai-tools/report/upload-url/", 200, upload_response)
        # PUT to the "S3" path
        self._set_path_response("/s3-bucket/object-key.json", 200, b"")
        # from-s3 notification
        self._set_path_response("/api/v1/ai-tools/report/from-s3/", 202, json.dumps({"status": "queued"}).encode())

        success, retryable = try_s3_upload(self.base_url, "k", self._payload())

        self.assertTrue(success)
        self.assertFalse(retryable)

        paths = [r["path"] for r in self.server.requests]
        self.assertEqual(paths, [
            "/api/v1/ai-tools/report/upload-url/",
            "/s3-bucket/object-key.json",
            "/api/v1/ai-tools/report/from-s3/",
        ])

        # The /from-s3/ payload must NOT contain the heavy `tools` list.
        from_s3_body = json.loads(self.server.requests[2]["body"])
        self.assertNotIn("tools", from_s3_body)
        self.assertEqual(from_s3_body["object_key"], "org/1/run/abc.json")
        self.assertEqual(from_s3_body["device_id"], "DEV-1")

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_app_name_forwarded_to_step3(self):
        """app_name on the payload must reach the /from-s3/ notification body."""
        upload_url = f"{self.base_url}/s3-bucket/object-key.json"
        upload_response = json.dumps({
            "upload_url": upload_url,
            "object_key": "org/1/run/abc.json",
        }).encode()

        self._set_path_response("/api/v1/ai-tools/report/upload-url/", 200, upload_response)
        self._set_path_response("/s3-bucket/object-key.json", 200, b"")
        self._set_path_response("/api/v1/ai-tools/report/from-s3/", 202, b"")

        payload = self._payload()
        payload["app_name"] = "JumpCloud"

        success, _ = try_s3_upload(self.base_url, "k", payload)
        self.assertTrue(success)

        from_s3_request = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/from-s3/"
        )
        body = json.loads(from_s3_request["body"])
        self.assertEqual(body["app_name"], "JumpCloud")

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_step1_503_signals_fallback(self):
        """503 from upload-url means S3 not configured backend-side."""
        self._set_path_response("/api/v1/ai-tools/report/upload-url/", 503, b'{"error":"S3 not configured"}')

        success, retryable = try_s3_upload(self.base_url, "k", self._payload())

        self.assertFalse(success)
        self.assertTrue(retryable)
        # Only one request — no PUT, no notify.
        self.assertEqual(len(self.server.requests), 1)

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_step2_failure_signals_fallback(self):
        """Server returns 500 on the PUT → fall back."""
        upload_url = f"{self.base_url}/s3-bucket/object-key.json"
        self._set_path_response(
            "/api/v1/ai-tools/report/upload-url/", 200,
            json.dumps({"upload_url": upload_url, "object_key": "org/1/run/abc.json"}).encode(),
        )
        self._set_path_response("/s3-bucket/object-key.json", 500, b"")

        success, retryable = try_s3_upload(self.base_url, "k", self._payload())

        self.assertFalse(success)
        self.assertTrue(retryable)
        # Step 3 should NOT have run after step 2 failed.
        paths = [r["path"] for r in self.server.requests]
        self.assertNotIn("/api/v1/ai-tools/report/from-s3/", paths)

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_step3_failure_signals_fallback(self):
        """Backend rejects the from-s3 notification → fall back."""
        upload_url = f"{self.base_url}/s3-bucket/object-key.json"
        self._set_path_response(
            "/api/v1/ai-tools/report/upload-url/", 200,
            json.dumps({"upload_url": upload_url, "object_key": "org/1/run/abc.json"}).encode(),
        )
        self._set_path_response("/s3-bucket/object-key.json", 200, b"")
        self._set_path_response("/api/v1/ai-tools/report/from-s3/", 400, b'{"error":"bad"}')

        success, retryable = try_s3_upload(self.base_url, "k", self._payload())

        self.assertFalse(success)
        self.assertTrue(retryable)


# ────────────────────────────────────────────────────────────────────
# Tests for the should_use_s3 gate
# ────────────────────────────────────────────────────────────────────
class TestShouldUseS3(unittest.TestCase):
    def test_data_report_uses_s3(self):
        self.assertTrue(should_use_s3({"tools": [{"name": "Cursor"}]}))

    def test_scan_event_skips_s3(self):
        self.assertFalse(should_use_s3({"scan_event": "in_progress", "tools": [{"name": "x"}]}))

    def test_empty_tools_skips_s3(self):
        self.assertFalse(should_use_s3({"tools": []}))

    def test_missing_tools_skips_s3(self):
        self.assertFalse(should_use_s3({}))

    def test_non_list_tools_skips_s3(self):
        self.assertFalse(should_use_s3({"tools": "not-a-list"}))


# ────────────────────────────────────────────────────────────────────
# Integration: send_report_to_backend tries S3 first, falls back on failure
# ────────────────────────────────────────────────────────────────────
class TestSendReportS3Integration(_ServerMixin, unittest.TestCase):
    def _data_report(self):
        return {
            "device_id": "DEV-1",
            "system_user": "alice",
            "home_user": "alice",
            "run_id": str(uuid.uuid4()),
            "tools": [{"name": "Cursor", "projects": []}],
        }

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_s3_success_short_circuits_legacy_path(self, _sleep):
        upload_url = f"{self.base_url}/s3-bucket/object-key.json"
        self._set_path_response(
            "/api/v1/ai-tools/report/upload-url/", 200,
            json.dumps({"upload_url": upload_url, "object_key": "org/1/run/abc.json"}).encode(),
        )
        self._set_path_response("/s3-bucket/object-key.json", 200, b"")
        self._set_path_response(
            "/api/v1/ai-tools/report/from-s3/", 202,
            json.dumps({"status": "queued"}).encode(),
        )

        success, retryable = send_report_to_backend(
            self.base_url, "k", self._data_report(),
        )

        self.assertTrue(success)
        self.assertFalse(retryable)
        # Legacy /api/v1/ai-tools/report/ should NOT have been hit.
        legacy_hits = [r for r in self.server.requests if r["path"] == "/api/v1/ai-tools/report/"]
        self.assertEqual(legacy_hits, [])

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_s3_failure_falls_back_to_legacy(self, _sleep):
        # Step 1 returns 503 → fall back
        self._set_path_response(
            "/api/v1/ai-tools/report/upload-url/", 503,
            b'{"error":"S3 not configured"}',
        )
        # Default 200 covers the legacy POST
        self.server.default_code = 200

        success, retryable = send_report_to_backend(
            self.base_url, "k", self._data_report(),
        )

        self.assertTrue(success)
        self.assertFalse(retryable)
        legacy_hits = [r for r in self.server.requests if r["path"] == "/api/v1/ai-tools/report/"]
        self.assertEqual(len(legacy_hits), 1)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_event_skips_s3_entirely(self, _sleep):
        self.server.default_code = 200
        scan_payload = {
            "device_id": "DEV-1",
            "run_id": str(uuid.uuid4()),
            "scan_event": "in_progress",
        }
        success, retryable = send_report_to_backend(
            self.base_url, "k", scan_payload,
        )
        self.assertTrue(success)
        self.assertFalse(retryable)
        s3_paths = [
            r for r in self.server.requests
            if r["path"].startswith("/api/v1/ai-tools/report/upload-url")
            or r["path"].startswith("/api/v1/ai-tools/report/from-s3")
        ]
        self.assertEqual(s3_paths, [])


# ────────────────────────────────────────────────────────────────────
# Hash compute determinism + ephemeral stripping
# ────────────────────────────────────────────────────────────────────
class TestComputePayloadHash(unittest.TestCase):
    def _tool(self):
        return {
            "name": "Cursor",
            "version": "0.42",
            "install_path": "/Applications/Cursor.app",
            "projects": [
                {
                    "path": "/x",
                    "rules": [
                        {
                            "file_name": ".rules",
                            "content": "use TS",
                            "size": 6,
                            "last_modified": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "skills": [
                        {
                            "file_name": "ship.md",
                            "skill_name": "ship",
                            "content": "# ship",
                            "size": 6,
                            "last_modified": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "mcpServers": [{"name": "github", "command": "npx"}],
                }
            ],
            "permissions": {"settings_source": "user", "raw_settings": {"a": 1}},
        }

    def test_deterministic_across_calls(self):
        t = self._tool()
        self.assertEqual(compute_payload_hash(t), compute_payload_hash(t))

    def test_hash_unchanged_when_only_last_modified_changes(self):
        t1 = self._tool()
        t2 = self._tool()
        t2["projects"][0]["rules"][0]["last_modified"] = "2099-12-31T23:59:59Z"
        t2["projects"][0]["skills"][0]["last_modified"] = "2099-12-31T23:59:59Z"
        self.assertEqual(compute_payload_hash(t1), compute_payload_hash(t2))

    def test_hash_changes_when_content_changes(self):
        t1 = self._tool()
        t2 = self._tool()
        t2["projects"][0]["rules"][0]["content"] = "use Rust"
        self.assertNotEqual(compute_payload_hash(t1), compute_payload_hash(t2))

    def test_hash_changes_when_mcp_server_added(self):
        t1 = self._tool()
        t2 = self._tool()
        t2["projects"][0]["mcpServers"].append({"name": "notion", "command": "uvx"})
        self.assertNotEqual(compute_payload_hash(t1), compute_payload_hash(t2))

    def test_hash_changes_when_permissions_change(self):
        t1 = self._tool()
        t2 = self._tool()
        t2["permissions"]["raw_settings"]["a"] = 2
        self.assertNotEqual(compute_payload_hash(t1), compute_payload_hash(t2))

    def test_hash_independent_of_key_order(self):
        # Two different orderings of the same dict → same hash.
        t1 = {"name": "Cursor", "version": "1.0"}
        t2 = {"version": "1.0", "name": "Cursor"}
        self.assertEqual(compute_payload_hash(t1), compute_payload_hash(t2))

    def test_hash_is_64_hex_chars(self):
        h = compute_payload_hash(self._tool())
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_strip_does_not_mutate_input(self):
        original = self._tool()
        snapshot = copy.deepcopy(original)
        _strip_ephemeral(original)
        self.assertEqual(original, snapshot)

    def test_handles_missing_optional_keys(self):
        compute_payload_hash({"name": "Bare"})
        compute_payload_hash({"name": "Empty", "projects": []})
        compute_payload_hash({"name": "Plain", "projects": [{"path": "/x"}]})


# ────────────────────────────────────────────────────────────────────
# Hash + tool_name forwarding in send_report_to_backend
# ────────────────────────────────────────────────────────────────────
class TestHashForwarding(_ServerMixin, unittest.TestCase):
    def _data_report(self):
        return {
            "device_id": "DEV-1",
            "system_user": "alice",
            "home_user": "alice",
            "run_id": str(uuid.uuid4()),
            "tools": [
                {
                    "name": "Cursor",
                    "version": "0.42",
                    "install_path": "/Applications/Cursor.app",
                    "projects": [],
                }
            ],
        }

    def _setup_s3_happy_path(self):
        upload_url = f"{self.base_url}/s3-bucket/key.json"
        self._set_path_response(
            "/api/v1/ai-tools/report/upload-url/", 200,
            json.dumps({"upload_url": upload_url, "object_key": "org/1/run/k.json"}).encode(),
        )
        self._set_path_response("/s3-bucket/key.json", 200, b"")
        self._set_path_response("/api/v1/ai-tools/report/from-s3/", 202, b"")

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_s3_path_includes_hash_and_tool_name_in_step3(self, _sleep):
        self._setup_s3_happy_path()
        report = self._data_report()
        expected_hash = compute_payload_hash(report["tools"][0])

        success, _ = send_report_to_backend(self.base_url, "k", report)
        self.assertTrue(success)

        from_s3_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/from-s3/"
        )
        body = json.loads(from_s3_req["body"])
        self.assertEqual(body["tool_name"], "Cursor")
        self.assertEqual(body["payload_hash"], expected_hash)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_legacy_path_includes_hash_and_tool_name_in_body(self, _sleep):
        # Force fallback: upload-url returns 503 (S3 disabled). Legacy POST receives the body.
        self._set_path_response("/api/v1/ai-tools/report/upload-url/", 503, b"{}")
        self.server.default_code = 200  # legacy POST succeeds

        report = self._data_report()
        expected_hash = compute_payload_hash(report["tools"][0])

        success, _ = send_report_to_backend(self.base_url, "k", report)
        self.assertTrue(success)

        legacy_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/"
        )
        body = json.loads(legacy_req["body"])
        self.assertEqual(body["tool_name"], "Cursor")
        self.assertEqual(body["payload_hash"], expected_hash)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_multi_tool_payload_skips_hash_stamping(self, _sleep):
        # When more than one tool is in the same payload we don't try to dedup
        # (the backend dedup key is per-tool). Hash fields stay absent.
        self._set_path_response("/api/v1/ai-tools/report/upload-url/", 503, b"{}")
        self.server.default_code = 200

        report = self._data_report()
        report["tools"].append({"name": "Claude Code", "install_path": "/x", "projects": []})

        success, _ = send_report_to_backend(self.base_url, "k", report)
        self.assertTrue(success)

        legacy_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/"
        )
        body = json.loads(legacy_req["body"])
        self.assertNotIn("payload_hash", body)
        self.assertNotIn("tool_name", body)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_scan_event_payload_skips_hash_stamping(self, _sleep):
        self.server.default_code = 200
        scan_payload = {
            "device_id": "DEV-1",
            "run_id": str(uuid.uuid4()),
            "scan_event": "in_progress",
        }
        success, _ = send_report_to_backend(self.base_url, "k", scan_payload)
        self.assertTrue(success)
        legacy_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/"
        )
        body = json.loads(legacy_req["body"])
        self.assertNotIn("payload_hash", body)
        self.assertNotIn("tool_name", body)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_missing_tool_name_skips_hash_stamping_entirely(self, _sleep):
        # Tool dict without a "name" key shouldn't half-stamp (hash set, name missing)
        # which would produce inconsistent body shapes across the S3 and legacy paths.
        self.server.default_code = 200
        report = self._data_report()
        report["tools"][0].pop("name", None)

        success, _ = send_report_to_backend(self.base_url, "k", report)
        self.assertTrue(success)
        legacy_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/"
        )
        body = json.loads(legacy_req["body"])
        self.assertNotIn("tool_name", body)
        self.assertNotIn("payload_hash", body)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_hash_failure_does_not_leave_partial_stamp(self, _sleep):
        # If compute_payload_hash raises, neither tool_name nor payload_hash
        # should make it into the body — the backend dedup needs both or neither.
        self.server.default_code = 200
        report = self._data_report()

        def boom(tool):
            raise RuntimeError("simulated hash failure")

        # The import happens inside send_report_to_backend, so patch the source.
        with patch("scripts.coding_discovery_tools.s3_uploader.compute_payload_hash", side_effect=boom):
            success, _ = send_report_to_backend(self.base_url, "k", report)

        self.assertTrue(success)
        legacy_req = next(
            r for r in self.server.requests
            if r["path"] == "/api/v1/ai-tools/report/"
        )
        body = json.loads(legacy_req["body"])
        self.assertNotIn("tool_name", body)
        self.assertNotIn("payload_hash", body)
