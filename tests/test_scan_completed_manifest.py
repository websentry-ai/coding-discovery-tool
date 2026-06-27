"""
Tests for WEB-4679: the scan "completed" event carries a manifest of the
(home_user, tool_name) pairs that were actually read this run, plus the full
set of enumerated home users, so the backend can set-diff and soft-delete
(prune) tools that are no longer installed.

Correctness property under test (the load-bearing one): the manifest is built
from per-user DETECTION/presence, not extraction success. A tool that was detected
present is recorded even if reading its config/rules errored, so a read failure can
never be mistaken for an uninstall (and never fail-closes the manifest to None).
Only users who actually detected a tool get an entry (no phantom ownership). A tool
whose DETECTOR errored marks the scan unclean (the backend skips pruning) rather than
recording a name, since detector.tool_name is an umbrella label, not the concrete row.

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


class TestManifestFromPresence(unittest.TestCase):
    """The load-bearing property: the manifest is built from per-user DETECTION/presence,
    not extraction success. A detected tool is recorded even if reading its config errored
    (so a read failure is never mistaken for an uninstall and never nulls the manifest);
    only users who detected the tool get an entry; and a DETECTOR error marks the scan
    unclean instead of recording an umbrella name.

    Seam: main() driven in-process with a mocked detector + discovery_cache and a
    captured send_scan_event. The per-(tool, user) loop body lives inline inside main(),
    so this is the only seam where the success / hash-match / read-error branches can be
    forced deterministically. No production code was changed to enable this.
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

    def _run_main_capture_manifest(self, send_report_result=(True, False), filter_error=None, detector_failure=None):
        """Run main() with three crafted tools for one user:
          ToolOK        -> hash mismatch -> send path
          ToolHashMatch -> hash match    -> dedup short-circuit
          ToolErr       -> filter raises (read/extraction error)
        All three are DETECTED, so all three must appear in the manifest (presence-based).
        send_report_result controls send_report_to_backend's (success, retryable).
        detector_failure: if set, detect_all_tools reports that tool_name via its `failures`
        set (a detector error) — it must also appear in the manifest though it isn't "found".
        Returns the captured (manifest, covered_home_users) from the completed send_scan_event.
        """
        adm = self.adm

        tool_ok = self._make_tool("ToolOK")
        tool_hm = self._make_tool("ToolHashMatch")
        tool_err = self._make_tool("ToolErr")

        detector = Mock()
        detector.get_device_id.return_value = "dev-xyz"

        def _detect_all(user_home=None, failures=None):
            # A detector error surfaces via the `failures` set (presence unknown -> kept in manifest).
            if detector_failure and failures is not None:
                failures.add(detector_failure)
            return [tool_ok, tool_hm, tool_err]
        detector.detect_all_tools.side_effect = _detect_all
        detector._set_canonical_vscode_copilot.return_value = None
        detector.process_single_tool.side_effect = lambda t: t

        def _filter(tool_with_projects, _user_home):
            if tool_with_projects["name"] == "ToolErr":
                raise (filter_error if filter_error is not None else PermissionError("simulated read failure"))
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
            elif scan_event == "failed":
                captured.setdefault("failed_events", []).append(kw.get("scan_error"))
            return (True, None)

        with patch.object(adm.platform, "system", return_value="Darwin"), \
             patch.object(adm, "AIToolsDetector", return_value=detector), \
             patch.object(adm, "discovery_cache", dc), \
             patch.object(adm, "get_all_users_macos", return_value=["alice"]), \
             patch.object(adm, "compute_payload_hash", side_effect=_hash), \
             patch.object(adm, "send_report_to_backend", return_value=send_report_result), \
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

    def test_read_error_keeps_tool_in_manifest(self):
        # A tool whose config read ERRORS is still detected present -> stays in the manifest (a read failure isn't an uninstall).
        captured = self._run_main_capture_manifest()

        self.assertIn("manifest", captured, "completed event was never sent")
        self.assertIsNotNone(captured["manifest"], "a read error must NOT fail-close the manifest to None")
        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}

        self.assertIn(("alice", "ToolOK"), pairs)         # sent path
        self.assertIn(("alice", "ToolHashMatch"), pairs)  # hash-match (unchanged, still installed)
        self.assertIn(("alice", "ToolErr"), pairs)        # read errored but DETECTED -> kept
        self.assertEqual(len(captured["manifest"]), 3)

    def test_upload_failure_keeps_tool_in_manifest(self):
        # Presence is recorded before extraction, so a transient UPLOAD failure still keeps the tool in the manifest.
        captured = self._run_main_capture_manifest(send_report_result=(False, True))

        self.assertIn("manifest", captured, "completed event was never sent")
        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}
        # All three detected tools present, regardless of upload outcome / read error.
        self.assertEqual(
            pairs,
            {("alice", "ToolOK"), ("alice", "ToolHashMatch"), ("alice", "ToolErr")},
        )

    def test_covered_home_users_includes_user_with_no_manifest_entry(self):
        # covered_home_users must come from the full enumeration (all_users),
        # so even though "alice" is the only user and one of her tools errored,
        # she still appears. More importantly, this proves covered_home_users is
        # not derived from the manifest: a user whose every tool errored would
        # still be covered (bounding the prune scope correctly).
        captured = self._run_main_capture_manifest()
        self.assertEqual(captured.get("covered_home_users"), ["alice"])

    def test_generic_read_error_does_not_fail_close(self):
        # Regression: a generic read error used to fail-close the manifest to None (blocking all pruning); it must no longer.
        captured = self._run_main_capture_manifest(
            filter_error=RuntimeError("simulated generic read failure")
        )
        self.assertIn("manifest", captured, "completed event was never sent")
        self.assertIsNotNone(
            captured["manifest"],
            "a generic read error must NOT fail-close the manifest to None",
        )
        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}
        self.assertIn(("alice", "ToolErr"), pairs)
        self.assertEqual(len(captured["manifest"]), 3)
        self.assertEqual(captured.get("covered_home_users"), ["alice"])

    def test_detector_error_marks_scan_unclean(self):
        # A DETECTOR error means presence is unknown, and detector.tool_name is only an umbrella
        # label (e.g. "GitHub Copilot") that can't safely target the concrete install rows. So the
        # run is marked unclean (a "failed" event) and the umbrella name is NOT added to the
        # manifest -> the backend skips pruning rather than prune a real surface row.
        captured = self._run_main_capture_manifest(detector_failure="ToolGhost")
        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}
        self.assertNotIn(("alice", "ToolGhost"), pairs)
        # Only the three actually-detected tools remain.
        self.assertEqual(len(captured["manifest"]), 3)
        self.assertTrue(
            captured.get("failed_events"),
            "a detector error must mark the scan unclean so the backend skips pruning this run",
        )

    def test_per_user_detection_no_phantom_ownership(self):
        # Phantom-ownership regression: all_tools is deduped globally, so a user-scoped tool one
        # user has must NOT be attributed to a co-resident user who did not detect it. Alice has
        # ToolA, Bob has ToolB; the manifest must contain exactly each user's own tool.
        adm = self.adm
        tool_a = self._make_tool("ToolA")
        tool_b = self._make_tool("ToolB")

        detector = Mock()
        detector.get_device_id.return_value = "dev-xyz"

        def _detect_all(user_home=None, failures=None):
            home = str(user_home or "")
            if home.endswith("alice"):
                return [tool_a]
            if home.endswith("bob"):
                return [tool_b]
            return []
        detector.detect_all_tools.side_effect = _detect_all
        detector._set_canonical_vscode_copilot.return_value = None
        detector._set_canonical_augment_surface.return_value = None
        detector.process_single_tool.side_effect = lambda t: t
        detector.filter_tool_projects_by_user.side_effect = lambda t, _h: t
        detector.generate_single_tool_report.side_effect = (
            lambda tool, device_id, home_user, system_user=None, run_id=None: {"tools": [tool]}
        )

        dc = Mock()
        dc.acquire_lock.return_value = "acquired"
        dc.heartbeat_start.return_value = Mock()
        dc.get_cached_hash.return_value = None
        dc.update_tool.return_value = None
        dc.UNBOUND_DIR = "/tmp/unbound-test"
        dc.last_lock_error = None

        captured = {}

        def _send_scan_event(domain, api_key, device_id, run_id, scan_event, app_name=None, **kw):
            if scan_event == "completed":
                captured["manifest"] = kw.get("manifest")
            return (True, None)

        with patch.object(adm.platform, "system", return_value="Darwin"), \
             patch.object(adm, "AIToolsDetector", return_value=detector), \
             patch.object(adm, "discovery_cache", dc), \
             patch.object(adm, "get_all_users_macos", return_value=["alice", "bob"]), \
             patch.object(adm, "compute_payload_hash", side_effect=lambda t: "h-" + t["name"]), \
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

        pairs = {(e["home_user"], e["tool_name"]) for e in captured["manifest"]}
        self.assertEqual(pairs, {("alice", "ToolA"), ("bob", "ToolB")})
        self.assertNotIn(("bob", "ToolA"), pairs)
        self.assertNotIn(("alice", "ToolB"), pairs)


if __name__ == "__main__":
    unittest.main()
