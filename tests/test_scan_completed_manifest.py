"""WEB-4679: the "completed" scan event carries a manifest of detected (home_user, tool_name)
pairs + the covered home users, so the backend can set-diff and prune what's gone.

Properties: the manifest is built from per-user DETECTION (not extraction success), so a read
error keeps a detected tool; only users who detected a tool get an entry (no phantom ownership);
a DETECTOR error sends no manifest (backend then skips pruning).

Seams: TestSendScanEventManifest (send_scan_event vs a localhost server), TestCompletedEventManifestCLI
(main() via subprocess), TestManifestFromPresence (main() in-process with a mocked detector),
TestJetBrainsNamingDeterminism (prune-key naming). Only HTTP/HOME/_SENTRY_DSN/discovery_cache are mocked.
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
from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import MacOSJetBrainsDetector

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
        # Empty manifest != "no manifest": it means "zero tools in scope" and must be sent
        # (key present), since the backend guard is `is not None`, not truthiness.
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
        # Throwaway HOME: isolated lock/cache so the run isn't blocked by a live lock and starts cold.
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
        # covered_home_users comes from the full enumeration, not the manifest's users — so it must
        # be a superset of the manifest's user set (asserted without forcing a zero-tool user).
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
    """Manifest is built from per-user DETECTION: a read error keeps a detected tool; only users who
    detected a tool get an entry; a DETECTOR error sends no manifest. Driven via main() in-process
    with a mocked detector + captured send_scan_event."""

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
        """Run main() with three detected tools for one user: ToolOK (send), ToolHashMatch (dedup
        skip), ToolErr (filter raises). detector_failure, if set, makes detect_all_tools report a
        detector error. Returns the captured (manifest, covered_home_users) from the completed event."""
        adm = self.adm

        tool_ok = self._make_tool("ToolOK")
        tool_hm = self._make_tool("ToolHashMatch")
        tool_err = self._make_tool("ToolErr")

        detector = Mock()
        detector.get_device_id.return_value = "dev-xyz"

        def _detect_all(user_home=None, failures=None):
            # A detector error surfaces via the `failures` set (-> scan marked incomplete).
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
        # covered_home_users comes from the full enumeration, not the manifest, so a user whose
        # tools all errored is still covered (bounds the prune scope correctly).
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

    def test_detector_error_sends_no_manifest(self):
        # A detector error means presence is unknown this run, so NO manifest is sent — atomically
        # on the completed event — and the backend (seeing no manifest) skips pruning entirely.
        captured = self._run_main_capture_manifest(detector_failure="ToolGhost")
        self.assertIsNone(
            captured["manifest"],
            "a detector error must send no manifest so the backend can't prune from a partial scan",
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


class TestJetBrainsNamingDeterminism(unittest.TestCase):
    """The JetBrains tool name is the backend prune key (matched exactly vs the manifest), so it
    must exclude version and license/plan — otherwise a version bump or Free<->Licensed change
    would orphan the install row and wrongly prune it. Fails if a change re-embeds them in the name.
    """

    def setUp(self):
        self.det = MacOSJetBrainsDetector()

    def test_display_name_is_version_free_and_stable_across_bumps(self):
        for folder in ("PyCharm2025.3", "PyCharm2025.3.1", "PyCharm2026.1"):
            name, version = self.det._parse_ide_name_and_version(folder)
            self.assertEqual(name, "PyCharm", f"{folder} must map to stable 'PyCharm'")
            self.assertNotIn(version, name, "version must not leak into the display name")
        self.assertEqual(
            self.det._parse_ide_name_and_version("IntelliJIdea2025.3")[0], "IntelliJ IDEA"
        )

    def test_mapping_values_carry_no_version_or_plan(self):
        for _prefix, name in MacOSJetBrainsDetector.IDE_NAME_MAPPING.items():
            self.assertNotRegex(name, r"\d", f"{name!r} must not embed a version digit")
            self.assertNotIn("(", name, f"{name!r} must not embed a (plan) suffix")

    def test_detected_tool_name_excludes_version_and_plan(self):
        # detect() sets name = display_name ONLY; version and plan stay in separate fields.
        fake_ide = {
            "display_name": "PyCharm", "version": "2025.3.1", "plan": "Licensed",
            "config_path": "/nonexistent/pycharm", "folder_name": "PyCharm2025.3.1",
        }
        with patch.object(self.det, "_scan_for_ides", return_value=[fake_ide]), \
                patch.object(self.det, "_get_plugins", return_value=[]):
            tools = self.det.detect()
        self.assertEqual(tools[0]["name"], "PyCharm", "prune key (name) must be the bare display_name")
        self.assertNotIn("2025", tools[0]["name"])
        self.assertNotIn("Licensed", tools[0]["name"])
        self.assertEqual(tools[0]["version"], "2025.3.1")
        self.assertEqual(tools[0]["plan"], "Licensed")


if __name__ == "__main__":
    unittest.main()
