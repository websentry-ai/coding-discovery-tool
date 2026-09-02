"""The "completed" scan event carries a manifest of detected (home_user, tool_name)
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

    def _run_cli(self, timeout=1800):
        env = os.environ.copy()
        # Throwaway HOME: isolated lock/cache so the run isn't blocked by a live lock and starts cold.
        env["HOME"] = tempfile.mkdtemp(prefix="discovery_home_")
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
        dc.last_lock_outcome = "acquired"
        dc.resumable_done.return_value = set()
        dc.read_run.return_value = {}

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
             patch.object(adm, "run_sweep", return_value=(0, 0, 0)), \
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
        # A detector error means presence is unknown this run, so NO manifest AND no covered scope
        # are sent — the backend then has no partial inventory/scope to prune from.
        captured = self._run_main_capture_manifest(detector_failure="ToolGhost")
        self.assertIsNone(captured["manifest"], "detector error must send no manifest")
        self.assertIsNone(captured.get("covered_home_users"), "no covered scope without an inventory")

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
        dc.last_lock_outcome = "acquired"
        dc.resumable_done.return_value = set()
        dc.read_run.return_value = {}

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
             patch.object(adm, "run_sweep", return_value=(0, 0, 0)), \
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


class TestExtensionDetectorPerUserScoping(unittest.TestCase):
    """Extension detectors must scope to the per-user home set by detect_tool_for_user, so an
    elevated multi-user scan can't attribute one user's extension to another (phantom ownership)."""

    def test_roo_detect_scopes_to_set_user_home(self):
        from scripts.coding_discovery_tools.macos.roo_code.roo_code import MacOSRooDetector
        det = MacOSRooDetector()
        det.user_home = Path("/Users/alice")
        with patch.object(det, "_detect_roo_for_user", return_value=[{"name": "Roo Code (Cursor)"}]) as m:
            result = det.detect()
        # Called exactly once with the SET home (not Path.home(), not per-/Users enumeration).
        m.assert_called_once_with(Path("/Users/alice"))
        self.assertEqual(result, [{"name": "Roo Code (Cursor)"}])


class TestWindowsIDEDetectorPerUserScoping(unittest.TestCase):
    """Cursor / Windsurf / Antigravity / Replit install per-user under %LOCALAPPDATA% on Windows
    (FOLDERID_UserProgramFiles is PERUSER), so a scan must probe the SCANNED user's home — not the
    scanner's, and not every user's. Machine-wide Program Files installs stay visible to everyone.

    Fails against the pre-fix code, where detect_tool_for_user short-circuited these four to a
    detector.detect() that resolved Path.home() / the all-users enumeration instead."""

    SCANNED = Path("C:/Users/alice")
    SCANNER = Path("C:/Users/bob")

    def _assert_scoped(self, candidates):
        joined = " ".join(candidates)
        self.assertIn("alice", joined)
        self.assertNotIn("bob", joined)
        self.assertTrue(any("Program Files" in c for c in candidates),
                        "machine-wide installs must stay in scope for every user")

    def test_cursor_scopes_to_set_user_home(self):
        from scripts.coding_discovery_tools.windows.cursor import cursor as mod
        det = mod.WindowsCursorDetector()
        det.user_home = self.SCANNED
        with patch.object(mod.Path, "home", return_value=self.SCANNER):
            self._assert_scoped([str(p) for p in det._get_search_paths()])

    def test_windsurf_scopes_to_set_user_home(self):
        from scripts.coding_discovery_tools.windows.windsurf import windsurf as mod
        det = mod.WindowsWindsurfDetector()
        det.user_home = self.SCANNED
        with patch.object(mod.Path, "home", return_value=self.SCANNER):
            self._assert_scoped([str(p) for p in det._get_search_paths()])

    def test_antigravity_scoped_skips_other_user_enumeration(self):
        from scripts.coding_discovery_tools.windows.antigravity import antigravity as mod
        det = mod.WindowsAntigravityDetector()
        det.user_home = self.SCANNED
        with patch.object(mod.Path, "home", return_value=self.SCANNER), \
             patch.object(mod, "is_running_as_admin", return_value=True), \
             patch.object(det, "_other_user_program_dirs",
                          return_value=[self.SCANNER / "AppData" / "Local" / "Programs"]):
            self._assert_scoped([str(p) for p in det._get_search_paths()])

    def test_replit_scoped_ignores_scanner_localappdata_and_other_users(self):
        from scripts.coding_discovery_tools.windows.replit import replit as mod
        det = mod.WindowsReplitDetector()
        det.user_home = self.SCANNED
        scanner_local = str(self.SCANNER / "AppData" / "Local")
        with patch.object(mod.Path, "home", return_value=self.SCANNER), \
             patch.object(mod, "is_running_as_admin", return_value=True), \
             patch.dict(os.environ, {"LOCALAPPDATA": scanner_local}), \
             patch.object(det, "_other_user_local_appdata_dirs", return_value=[Path(scanner_local)]), \
             patch.object(det, "_other_user_program_dirs", return_value=[Path(scanner_local) / "Programs"]):
            self._assert_scoped([str(p) for p in det._candidate_install_paths()])

    def test_unscoped_detectors_keep_scanner_home_and_all_users_enumeration(self):
        """No user_home set (legacy single-user path) -> unchanged behaviour."""
        from scripts.coding_discovery_tools.windows.cursor import cursor as cursor_mod
        from scripts.coding_discovery_tools.windows.antigravity import antigravity as ag_mod
        with patch.object(cursor_mod.Path, "home", return_value=self.SCANNER):
            self.assertIn("bob", " ".join(str(p) for p in cursor_mod.WindowsCursorDetector()._get_search_paths()))
        other = self.SCANNER / "AppData" / "Local" / "Programs"
        det = ag_mod.WindowsAntigravityDetector()
        with patch.object(ag_mod.Path, "home", return_value=self.SCANNED), \
             patch.object(ag_mod, "is_running_as_admin", return_value=True), \
             patch.object(det, "_other_user_program_dirs", return_value=[other]):
            self.assertIn(str(other / "antigravity"), [str(p) for p in det._get_search_paths()])


class TestWindowsDetectorProbePermissionSafety(unittest.TestCase):
    """Scoping points detect() at OTHER users' profile dirs, which Windows ACL-denies to a
    non-elevated scan (the scheduled task runs -RunLevel Limited). Path.exists() re-raises
    EACCES -- CPython only ignores ENOENT/ENOTDIR/EBADF/ELOOP -- so an unguarded probe both
    aborts before the machine-wide candidates AND marks the run incomplete, which stops the
    backend pruning the very phantom rows this scoping exists to remove."""

    CASES = (
        ("Cursor", "Cursor.exe", "resources/app"),
        ("Windsurf", "Windsurf.exe", "resources/app"),
        ("Antigravity", "Antigravity.exe", "resources"),
    )

    def _detector(self, label):
        from scripts.coding_discovery_tools.windows.cursor.cursor import WindowsCursorDetector
        from scripts.coding_discovery_tools.windows.windsurf.windsurf import WindowsWindsurfDetector
        from scripts.coding_discovery_tools.windows.antigravity.antigravity import WindowsAntigravityDetector
        return {"Cursor": WindowsCursorDetector, "Windsurf": WindowsWindsurfDetector,
                "Antigravity": WindowsAntigravityDetector}[label]()

    @staticmethod
    def _deny(denied_root, real_exists):
        """Path.exists side effect that denies one subtree the way Windows denies another
        user's profile. Injected rather than chmod'd: on Windows os.chmod only toggles the
        read-only bit and cannot deny traversal, so a chmod-based test passes there without
        ever entering the guard."""
        def exists(self, *args, **kwargs):
            if denied_root == self or denied_root in self.parents:
                raise PermissionError(13, "Access is denied")
            return real_exists(self, *args, **kwargs)
        return exists

    def test_denied_user_dir_does_not_hide_machine_wide_install(self):
        real_exists = Path.exists
        for label, exe, resources in self.CASES:
            with self.subTest(tool=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                profile = root / "Users" / "alice"
                denied = profile / "AppData" / "Local" / "Programs" / label
                machine = root / "Program Files" / label
                (machine / resources).mkdir(parents=True)
                (machine / exe).write_text("")

                det = self._detector(label)
                # Index 0 is the access-denied per-user dir, index 1 the machine-wide install.
                with patch.object(det, "_get_search_paths", return_value=[denied, machine]), \
                     patch.object(Path, "exists", self._deny(profile, real_exists)):
                    result = det.detect()  # must not raise
                self.assertIsNotNone(result, f"{label}: machine-wide install not reached")
                self.assertEqual(str(machine), str(result["install_path"]))

    def test_non_permission_oserror_still_propagates(self):
        """A transient I/O failure must NOT be swallowed into "not installed" — it has to reach
        detect_all_tools, which marks the run incomplete so the backend does not prune a real
        install off a probe that never actually ran."""
        def boom(self, *args, **kwargs):
            raise OSError(5, "I/O error")
        for label, _exe, _resources in self.CASES:
            with self.subTest(tool=label):
                det = self._detector(label)
                with patch.object(det, "_get_search_paths", return_value=[Path("/x") / label]), \
                     patch.object(Path, "exists", boom):
                    with self.assertRaises(OSError):
                        det.detect()

    def test_no_registered_windows_detector_raises_on_denied_home(self):
        """Whole-fleet invariant, not per-tool: ONE raising detector marks the entire run
        incomplete (detect_all_tools :475 -> incomplete_reasons -> manifest=None), and the
        backend refuses to prune on a null manifest. So a single unguarded probe anywhere
        disables pruning for every tool on the device. Also covers detectors added later."""
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
        from scripts.coding_discovery_tools.user_tool_detector import detect_tool_for_user

        real_exists = Path.exists
        with tempfile.TemporaryDirectory() as tmp:
            denied = Path(tmp) / "Users" / "alice"
            readable_scanner_home = Path(tmp) / "Users" / "bob"
            readable_scanner_home.mkdir(parents=True)

            raised = []
            for det in ToolDetectorFactory.create_all_tool_detectors("Windows"):
                with patch.object(Path, "exists", self._deny(denied, real_exists)), \
                     patch.object(Path, "home", lambda: readable_scanner_home):
                    try:
                        detect_tool_for_user(det, denied)
                    except Exception as exc:
                        raised.append(f"{det.tool_name}: {type(exc).__name__}")
            self.assertEqual([], raised, f"detectors raised on an access-denied home: {raised}")


class TestWindowsCopilotPerUserScoping(unittest.TestCase):
    """GitHub Copilot rows are emitted per host editor, and _detect_*_all_users() ignored the
    per-user home set by detect_tool_for_user -- so under admin every profile got the union of
    everyone's Copilot, and under a non-elevated scan every profile got the scanner's."""

    def setUp(self):
        from scripts.coding_discovery_tools.windows.github_copilot import detect_copilot as mod
        self.mod = mod
        self.det = mod.WindowsGitHubCopilotDetector()

    def test_vscode_scoped_to_set_user_home(self):
        self.det.user_home = Path("C:/Users/alice")
        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.det, "_detect_vscode_for_user", return_value=[{"name": "x"}]) as m:
            result = self.det._detect_vscode_all_users()
        # Called exactly once with the SET home -- not Path.home(), not the C:\Users sweep.
        m.assert_called_once_with(Path("C:/Users/alice"))
        self.assertEqual(result, [{"name": "x"}])

    def test_jetbrains_scoped_to_set_user_home(self):
        self.det.user_home = Path("C:/Users/alice")
        with patch.object(self.mod, "is_running_as_admin", return_value=True), \
             patch.object(self.det, "_detect_jetbrains_for_user", return_value=[{"name": "y"}]) as m:
            result = self.det._detect_jetbrains_all_users()
        m.assert_called_once_with(Path("C:/Users/alice"))
        self.assertEqual(result, [{"name": "y"}])

    def test_unscoped_keeps_all_users_sweep(self):
        """No user_home set (legacy single-user path) -> unchanged behaviour."""
        with patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.mod.Path, "home", return_value=Path("C:/Users/bob")), \
             patch.object(self.det, "_detect_vscode_for_user", return_value=[]) as m:
            self.det._detect_vscode_all_users()
        m.assert_called_once_with(Path("C:/Users/bob"))


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


class TestManifestKeyedByInstallPath(unittest.TestCase):
    """all_tools is keyed on name+path but the emission gate asked a name-only manifest, so every
    user holding ANY install of tool T was emitted for EVERY path of T on the machine. Both users
    genuinely have the tool -- these are real installs carrying another user's install_path, and
    since ingest is delete-then-create on (device, tool_name, home_user), last write wins."""

    ALICE = r"C:\Users\alice\.local\bin\claude.exe"
    BOB = r"C:\Users\bob\AppData\Roaming\npm\claude.cmd"
    SHARED = r"C:\Program Files\Claude\claude.exe"

    def _run(self, paths_by_user, tool_name="Claude Code", extra=None):
        """Drive main() with one tool per user at the given path. Returns (reports, manifest)."""
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        def _tool(path):
            return {"name": tool_name, "version": "1.0", "install_path": path,
                    "projects": [], **(extra or {})}

        def _detect_all(user_home=None, failures=None):
            for user, path in paths_by_user.items():
                if str(user_home or "").endswith(user):
                    return [_tool(path)]
            return []

        detector = Mock()
        detector.get_device_id.return_value = "dev-xyz"
        detector.detect_all_tools.side_effect = _detect_all
        detector._set_canonical_vscode_copilot.return_value = None
        detector._set_canonical_augment_surface.return_value = None
        detector._set_canonical_junie_surface.return_value = None
        detector.process_single_tool.side_effect = lambda t: t
        detector.filter_tool_projects_by_user.side_effect = lambda t, _h: t.copy()
        detector.generate_single_tool_report.side_effect = (
            lambda tool, device_id, home_user, system_user=None, run_id=None:
            {"home_user": home_user, "tools": [tool]})
        detector.skills_metrics = {}

        dc = Mock()
        dc.get_cached_hash.return_value = None
        dc.resumable_done.return_value = set()
        dc.read_run.return_value = {}

        reports, captured = [], {}

        def _send_report(domain, api_key, report, app_name, sentry_context=None):
            reports.append((report["home_user"], report["tools"][0].get("install_path")))
            return True, False

        def _send_scan_event(domain, api_key, device_id, run_id, scan_event, app_name=None, **kw):
            if scan_event == "completed":
                captured["manifest"] = kw.get("manifest")
            return (True, None)

        with patch.object(adm.platform, "system", return_value="Darwin"), \
             patch.object(adm, "AIToolsDetector", return_value=detector), \
             patch.object(adm, "discovery_cache", dc), \
             patch.object(adm, "get_all_users_macos", return_value=sorted(paths_by_user)), \
             patch.object(adm, "compute_payload_hash", side_effect=lambda t: "h"), \
             patch.object(adm, "send_report_to_backend", side_effect=_send_report), \
             patch.object(adm, "send_scan_event", side_effect=_send_scan_event), \
             patch.object(adm, "send_discovery_metrics", Mock()), \
             patch.object(adm, "run_sweep", return_value=(0, 0, 0)), \
             patch.object(adm, "load_pending_reports", return_value=[]), \
             patch.object(adm, "save_failed_reports", Mock()), \
             patch.object(adm, "report_to_sentry", Mock()), \
             patch.object(utils_mod, "_SENTRY_DSN", ""), \
             patch.object(sys, "argv", ["x", "--api-key", "k", "--domain", "http://127.0.0.1:1"]):
            try:
                adm.main()
            except SystemExit:
                pass
        return reports, captured.get("manifest")

    def test_same_tool_different_paths_emits_one_row_per_owner(self):
        """Pre-fix this emits 2 paths x 2 users = 4 reports, two of them carrying the other
        user's binary."""
        reports, _ = self._run({"alice": self.ALICE, "bob": self.BOB})
        self.assertEqual(
            sorted(reports), sorted([("alice", self.ALICE), ("bob", self.BOB)]),
            f"each user must be reported once, with their OWN install path; got {reports}")

    def test_shared_machine_wide_path_still_emits_for_every_user(self):
        """Path in the key must not over-correct: one machine-wide install is legitimately
        every user's, and all_tools dedups it to a single entry."""
        reports, _ = self._run({"alice": self.SHARED, "bob": self.SHARED})
        self.assertEqual(sorted(reports),
                         sorted([("alice", self.SHARED), ("bob", self.SHARED)]))

    def test_manifest_wire_format_unchanged_and_deduped(self):
        """The manifest stays [{home_user, tool_name}] -- reconcile reads only tool_name per
        home_user. Dedup is mandatory, not cosmetic: the backend does not dedup, and it refuses
        to prune once the manifest reaches MAX_MANIFEST_ENTRIES."""
        _, manifest = self._run({"alice": self.ALICE, "bob": self.BOB})
        self.assertEqual(manifest, [{"home_user": "alice", "tool_name": "Claude Code"},
                                    {"home_user": "bob", "tool_name": "Claude Code"}])

    def test_ownership_gate_discard_removes_the_manifest_entry(self):
        """set.discard() never raises, so a drifted key would silently leave the entry in the
        manifest and the phantom would never be pruned. Assert the entry is actually gone."""
        reports, manifest = self._run(
            {"alice": self.ALICE, "bob": self.ALICE},
            tool_name="GitHub Copilot CLI",
            extra={"_config_path": "/Users/alice/.copilot"})
        self.assertEqual(reports, [("alice", self.ALICE)])
        self.assertEqual(manifest, [{"home_user": "alice", "tool_name": "GitHub Copilot CLI"}])

    def test_filter_preserves_the_install_path_the_discard_key_depends_on(self):
        """The discard keys off tool_filtered; filter_tool_projects_by_user must keep
        install_path byte-identical or the gate silently stops suppressing."""
        from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        tool = {"name": "Claude Code", "install_path": self.ALICE,
                "projects": [{"path": "/Users/bob/proj"}]}
        filtered = AIToolsDetector.filter_tool_projects_by_user(None, tool, Path("/Users/alice"))
        self.assertEqual(self.ALICE, filtered["install_path"])
        self.assertEqual([], filtered["projects"])


if __name__ == "__main__":
    unittest.main()
