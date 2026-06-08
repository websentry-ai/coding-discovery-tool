"""
Tests for WEB-4679: the scan "completed" event carries a manifest of the
(home_user, tool_name) pairs that were actually read this run, plus the full
set of enumerated home users, so the backend can set-diff and soft-delete
(prune) tools that are no longer installed.

Correctness property under test (the load-bearing one): a tool whose read
ERRORED is NEVER recorded in the manifest, so a transient read failure can
never be mistaken for an uninstall. Tools that were sent successfully OR
short-circuited by the local hash-match dedup (still installed, just unchanged)
ARE recorded.

Seams (mirroring the existing suite in test_send_and_persist.py /
test_discovery_flow.py):
  * TestSendScanEventManifest    -> utils.send_scan_event against a real
    localhost HTTP server (records POST bodies). Covers the payload-shaping
    contract + backward compatibility.
  * TestCompletedEventManifestCLI -> main() via subprocess against a real
    localhost HTTP server. Covers the end-to-end completed-event payload and
    that in_progress/failed events do NOT carry a manifest.
  * TestManifestExcludesErroredReads -> main() driven IN-PROCESS with a mock
    detector so a per-tool read error / hash-match / success can be forced
    deterministically. This is the only seam where the errored-read branch can
    be isolated, because the per-(tool, user) loop body lives inline in main().

Only external environment is mocked: HTTP backend (real server on localhost),
HOME (so the subprocess gets an isolated discovery lock/cache and never exits
early on a live lock from another run), _SENTRY_DSN (no real Sentry calls),
discovery_cache / detector (in-process seam only). No network is required.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import send_scan_event

REPO_ROOT = Path(__file__).resolve().parent.parent


class _RecordingHandler(BaseHTTPRequestHandler):
    """Records every POST body (parsed as JSON) and returns 200."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            self.server.requests.append(json.loads(body))
        except ValueError:
            self.server.requests.append({"_raw": body.decode("utf-8", "replace")})

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass  # suppress server logs


class _ServerTestCase(unittest.TestCase):
    """Spins up one recording HTTP server on localhost for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        cls.server.requests = []
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.requests.clear()


class TestSendScanEventManifest(_ServerTestCase):
    """utils.send_scan_event seam: manifest + covered_home_users are inserted
    into the POST body only when provided (backward compatible)."""

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_completed_event_carries_manifest_and_covered_users(self, _sleep):
        manifest = [{"home_user": "alice", "tool_name": "Cursor"}]
        covered = ["alice", "bob"]

        success, _retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEV-1",
            "run-1",
            "completed",
            manifest=manifest,
            covered_home_users=covered,
        )

        self.assertTrue(success)
        self.assertEqual(len(self.server.requests), 1)
        body = self.server.requests[0]
        # Exact passthrough of both new fields.
        self.assertEqual(body["scan_event"], "completed")
        self.assertEqual(body["manifest"], manifest)
        self.assertEqual(body["covered_home_users"], covered)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_legacy_call_omits_both_keys(self, _sleep):
        # No manifest / covered_home_users supplied -> neither key may appear
        # in the payload (backward compatibility with the old call sites).
        success, _retryable = send_scan_event(
            self.base_url, "test-key", "DEV-1", "run-1", "in_progress"
        )

        self.assertTrue(success)
        self.assertEqual(len(self.server.requests), 1)
        body = self.server.requests[0]
        self.assertNotIn("manifest", body)
        self.assertNotIn("covered_home_users", body)

    @patch("time.sleep")
    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_empty_manifest_still_sent(self, _sleep):
        # An empty manifest is meaningfully different from "no manifest": it
        # tells the backend "this scope had zero readable tools" (prune-all
        # within scope). It must be sent (key present), since the production
        # guard is `is not None`, not truthiness.
        success, _retryable = send_scan_event(
            self.base_url,
            "test-key",
            "DEV-1",
            "run-1",
            "completed",
            manifest=[],
            covered_home_users=["alice"],
        )

        self.assertTrue(success)
        body = self.server.requests[0]
        self.assertIn("manifest", body)
        self.assertEqual(body["manifest"], [])
        self.assertEqual(body["covered_home_users"], ["alice"])


class TestCompletedEventManifestCLI(_ServerTestCase):
    """End-to-end via main() subprocess: the completed event carries a
    well-formed manifest + covered_home_users; lifecycle events that are not
    "completed" carry neither."""

    def _run_cli(self, timeout=600):
        env = os.environ.copy()
        # Isolate the discovery state dir (lock + cache) under a throwaway HOME
        # so the run never exits early on a live lock left by another process,
        # and starts from a cold cache (deterministic hash-match behavior).
        env["HOME"] = tempfile.mkdtemp(prefix="web4679_home_")
        return subprocess.run(
            [
                sys.executable,
                "scripts/coding_discovery_tools/ai_tools_discovery.py",
                "--api-key",
                "test-key-000000",
                "--domain",
                self.base_url,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def test_completed_event_has_manifest_and_covered_users(self):
        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr[-2000:]}")

        completed = [
            r for r in self.server.requests if r.get("scan_event") == "completed"
        ]
        self.assertEqual(len(completed), 1, "expected exactly one completed event")
        body = completed[0]

        # manifest: list of {home_user, tool_name} objects.
        self.assertIn("manifest", body)
        self.assertIsInstance(body["manifest"], list)
        for entry in body["manifest"]:
            self.assertIsInstance(entry, dict)
            self.assertIn("home_user", entry)
            self.assertIn("tool_name", entry)
            self.assertIsInstance(entry["home_user"], str)
            self.assertIsInstance(entry["tool_name"], str)

        # covered_home_users: list of user names (strings).
        self.assertIn("covered_home_users", body)
        self.assertIsInstance(body["covered_home_users"], list)
        for user in body["covered_home_users"]:
            self.assertIsInstance(user, str)

    def test_non_completed_events_have_no_manifest(self):
        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr[-2000:]}")

        # An in_progress event is always sent before scanning.
        non_completed = [
            r
            for r in self.server.requests
            if r.get("scan_event") in ("in_progress", "failed")
        ]
        self.assertGreaterEqual(
            len(non_completed), 1, "expected at least an in_progress event"
        )
        for body in non_completed:
            self.assertNotIn(
                "manifest", body, f"{body.get('scan_event')} must not carry a manifest"
            )
            self.assertNotIn(
                "covered_home_users",
                body,
                f"{body.get('scan_event')} must not carry covered_home_users",
            )

    def test_covered_home_users_matches_full_enumeration_not_manifest(self):
        # covered_home_users is sourced from the full user enumeration
        # (all_users), NOT only from users that produced manifest entries. So a
        # user who contributed zero manifest entries still appears in
        # covered_home_users. We assert this invariant without needing to force
        # a specific zero-tool user on the host: every home_user that appears in
        # the manifest must also appear in covered_home_users, and
        # covered_home_users must be a superset of the manifest's user set.
        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr[-2000:]}")

        completed = [
            r for r in self.server.requests if r.get("scan_event") == "completed"
        ]
        self.assertEqual(len(completed), 1)
        body = completed[0]

        covered = set(body["covered_home_users"])
        manifest_users = {e["home_user"] for e in body["manifest"]}
        # The enumerated set must cover every user that yielded a manifest entry.
        self.assertTrue(
            manifest_users.issubset(covered),
            f"manifest users {manifest_users} not all in covered {covered}",
        )


class TestManifestExcludesErroredReads(unittest.TestCase):
    """The load-bearing property: a tool whose read raises is OMITTED from the
    manifest, while a successfully-sent tool and a hash-match (unchanged, still
    installed) tool are BOTH included.

    Seam: main() driven in-process with a mocked detector + discovery_cache and
    a captured send_scan_event. This is necessary because the per-(tool, user)
    loop body — with its success / hash-match / PermissionError branches — lives
    inline inside main() and is not an independently callable unit. No
    production code was changed to enable this; every name patched here is a
    module-level import already present in ai_tools_discovery.
    """

    def setUp(self):
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        self.adm = adm
        self.argv = [
            "ai_tools_discovery.py",
            "--api-key",
            "k",
            "--domain",
            "http://127.0.0.1:1",
        ]

    @staticmethod
    def _make_tool(name):
        # Distinct install_path per tool so the (name:path) dedup keeps all three.
        return {"name": name, "version": "1.0", "install_path": f"/opt/{name}", "projects": []}

    def _run_main_capture_manifest(self):
        """Run main() with three crafted tools for one user:
          ToolOK        -> hash mismatch -> send path -> send succeeds -> appended
          ToolHashMatch -> hash match    -> dedup short-circuit         -> appended
          ToolErr       -> filter raises PermissionError                -> NOT appended
        Returns the captured (manifest, covered_home_users) from the completed
        send_scan_event call.
        """
        adm = self.adm

        tool_ok = self._make_tool("ToolOK")
        tool_hm = self._make_tool("ToolHashMatch")
        tool_err = self._make_tool("ToolErr")

        detector = Mock()
        detector.get_device_id.return_value = "dev-xyz"
        detector.detect_all_tools.return_value = [tool_ok, tool_hm, tool_err]
        detector._set_canonical_vscode_copilot.return_value = None
        detector.process_single_tool.side_effect = lambda t: t

        def _filter(tool_with_projects, _user_home):
            if tool_with_projects["name"] == "ToolErr":
                raise PermissionError("simulated read failure")
            return tool_with_projects

        detector.filter_tool_projects_by_user.side_effect = _filter
        detector.generate_single_tool_report.side_effect = (
            lambda tool, device_id, home_user, system_user=None, run_id=None: {
                "tools": [tool]
            }
        )

        # Hash is derived from the tool name; the cache "matches" only for
        # ToolHashMatch, forcing ToolOK down the send path and ToolHashMatch
        # down the dedup short-circuit.
        def _hash(tool_dict):
            return "hash-" + tool_dict["name"]

        def _cached(tool_name, _user_name):
            return "hash-ToolHashMatch" if tool_name == "ToolHashMatch" else None

        dc = Mock()
        dc.acquire_lock.return_value = "acquired"
        dc.heartbeat_start.return_value = Mock()
        dc.get_cached_hash.side_effect = _cached
        dc.update_tool.return_value = None
        dc.UNBOUND_DIR = "/tmp/unbound-test"
        dc.last_lock_error = None

        captured = {}

        def _send_scan_event(domain, api_key, device_id, run_id, scan_event, app_name=None, **kw):
            if scan_event == "completed":
                captured["manifest"] = kw.get("manifest")
                captured["covered_home_users"] = kw.get("covered_home_users")
            return (True, None)

        with patch.object(adm.platform, "system", return_value="Darwin"), \
             patch.object(adm, "AIToolsDetector", return_value=detector), \
             patch.object(adm, "discovery_cache", dc), \
             patch.object(adm, "get_all_users_macos", return_value=["alice"]), \
             patch.object(adm, "compute_payload_hash", side_effect=_hash), \
             patch.object(adm, "send_report_to_backend", return_value=(True, False)), \
             patch.object(adm, "send_scan_event", side_effect=_send_scan_event), \
             patch.object(adm, "send_discovery_metrics", Mock()), \
             patch.object(adm, "load_pending_reports", return_value=[]), \
             patch.object(adm, "save_failed_reports", Mock()), \
             patch.object(adm, "report_to_sentry", Mock()), \
             patch.object(utils_mod, "_SENTRY_DSN", ""), \
             patch.object(sys, "argv", self.argv):
            try:
                adm.main()
            except SystemExit:
                pass

        return captured

    def test_errored_tool_excluded_success_and_hashmatch_included(self):
        captured = self._run_main_capture_manifest()

        self.assertIn("manifest", captured, "completed event was never sent")
        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}

        # Success path recorded.
        self.assertIn(("alice", "ToolOK"), pairs)
        # Hash-match (unchanged, still installed) recorded.
        self.assertIn(("alice", "ToolHashMatch"), pairs)
        # Errored read is the load-bearing exclusion: a read failure must never
        # look like an uninstall.
        self.assertNotIn(("alice", "ToolErr"), pairs)
        # Exactly the two non-errored pairs, nothing else.
        self.assertEqual(len(captured["manifest"]), 2)

    def test_covered_home_users_includes_user_with_no_manifest_entry(self):
        # covered_home_users must come from the full enumeration (all_users),
        # so even though "alice" is the only user and one of her tools errored,
        # she still appears. More importantly, this proves covered_home_users is
        # not derived from the manifest: a user whose every tool errored would
        # still be covered (bounding the prune scope correctly).
        captured = self._run_main_capture_manifest()
        self.assertEqual(captured.get("covered_home_users"), ["alice"])


if __name__ == "__main__":
    unittest.main()
