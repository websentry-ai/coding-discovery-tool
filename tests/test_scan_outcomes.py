"""
WEB-6392 step 5: every scan leaves a record, and a live scan never looks dead.

Seams:
  - TestLockHeldRunIsRecorded: the real CLI against a localhost backend, with a
    live lock in a throwaway HOME. A run turned away by the lock must still tell
    the backend it was skipped, print the line setup reads, and exit as before.
  - TestScanHeartbeats: main() in-process with the detector and the transport
    mocked. While tools are detected the run keeps re-sending in_progress for its
    run_id, and it goes quiet before its outcome is reported.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIPPED_LINE = "Scan skipped: another scan is running"


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append({"path": self.path, "body": json.loads(body)})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "scan_skipped"}')

    def log_message(self, *args):
        pass


def _child_env(home):
    env = os.environ.copy()
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["AI_DISCOVERY_SENTRY_DSN"] = ""
    env["AI_DISCOVERY_QUEUE_FILE"] = str(Path(home) / "report-queue.json")
    return env


class TestLockHeldRunIsRecorded(unittest.TestCase):

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.home = tempfile.mkdtemp(prefix="scan-outcomes-home-")
        state = Path(self.home) / ".unbound"
        state.mkdir(mode=0o700)
        # Held by a live process (this one) with a fresh heartbeat: genuine contention.
        self.lock = state / "discovery.lock"
        self.lock.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self):
        return subprocess.run(
            [sys.executable, "scripts/coding_discovery_tools/ai_tools_discovery.py",
             "--api-key", "test-key-000000",
             "--domain", f"http://127.0.0.1:{self.server.server_address[1]}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
            env=_child_env(self.home),
        )

    def test_skipped_run_is_reported_printed_and_exits_zero(self):
        result = self._run()

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn(SKIPPED_LINE, result.stdout.splitlines())
        events = [r["body"] for r in self.server.requests]
        self.assertEqual(len(events), 1, f"expected one skipped event, got {events}")
        event = events[0]
        self.assertEqual(self.server.requests[0]["path"], "/api/v1/ai-tools/report/")
        self.assertEqual(event["scan_event"], "skipped")
        self.assertEqual(event["reason"], "another scan is running")
        self.assertEqual(uuid.UUID(event["run_id"]).version, 4)
        self.assertTrue(event["device_id"])
        # The lock belongs to the running scan; a skipped run must leave it alone.
        self.assertTrue(self.lock.exists(), "the skipped run removed another run's lock")

    def test_unreachable_backend_still_prints_and_exits_zero(self):
        self.server.shutdown()
        self.server.server_close()
        result = self._run()

        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn(SKIPPED_LINE, result.stdout.splitlines())


class TestScanHeartbeats(unittest.TestCase):

    def setUp(self):
        import scripts.coding_discovery_tools.ai_tools_discovery as adm
        self.adm = adm
        self.events = []
        self._events_lock = threading.Lock()

    def _record(self, *args, **kwargs):
        with self._events_lock:
            self.events.append({"run_id": args[3], "event": args[4]})
        return (True, None)

    def _run_main(self, detect, send=None, *extra_argv, sentry_dsn="", domain="http://127.0.0.1:1"):
        adm = self.adm
        detector = Mock()
        detector.get_device_id.return_value = "dev"
        detector.detect_all_tools.side_effect = detect
        argv = ["ai_tools_discovery.py", "--api-key", "k", "--domain", domain, *extra_argv]
        patches = [
            patch.object(adm, "SCAN_HEARTBEAT_INTERVAL_SECONDS", 0.05),
            patch.object(adm.platform, "system", return_value="Linux"),
            patch.object(adm.discovery_cache, "acquire_lock", return_value="acquired"),
            patch.object(adm.discovery_cache, "heartbeat_start", Mock(return_value=threading.Event())),
            patch.object(adm.discovery_cache, "release_lock", Mock()),
            patch.object(adm.discovery_cache, "start_run", Mock()),
            patch.object(adm.discovery_cache, "resumable_done", Mock(return_value=set())),
            patch.object(adm.discovery_cache, "mark_run_completed", Mock()),
            patch.object(adm, "AIToolsDetector", return_value=detector),
            patch.object(adm, "send_scan_event", Mock(side_effect=send or self._record)),
            patch.object(adm, "send_discovery_metrics", Mock()),
            patch.object(adm, "run_sweep", Mock(return_value=(0, 0, 0))),
            patch.object(adm, "load_pending_reports", return_value=[]),
            patch.object(adm, "save_failed_reports", Mock()),
            patch.object(adm, "get_all_users_linux", return_value=["alice"]),
            patch.object(adm, "home_is_readable", return_value=True),
            patch.object(adm, "get_audit_user", return_value="alice"),
            patch.object(adm, "report_to_sentry", Mock()),
            patch.object(utils_mod, "_SENTRY_DSN", sentry_dsn),
            patch.object(sys, "argv", argv),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            try:
                adm.main()
                return 0
            except SystemExit as e:
                return e.code

    def _slow_detect(self, *args, **kwargs):
        time.sleep(0.5)
        return []

    def _assert_heartbeat_stopped(self):
        alive = [t for t in threading.enumerate() if t.name == "scan-heartbeat"]
        self.assertEqual(alive, [], "the heartbeat outlived the run")

    def test_long_detection_keeps_sending_in_progress_until_completed(self):
        code = self._run_main(self._slow_detect)

        self.assertIn(code, (0, None))
        names = [e["event"] for e in self.events]
        self.assertEqual(names[-1], "completed")
        # One in_progress at start plus repeats while detection ran.
        self.assertGreaterEqual(names.count("in_progress"), 3, names)
        self.assertEqual(len({e["run_id"] for e in self.events}), 1, "heartbeats must reuse the run_id")
        time.sleep(0.2)  # a heartbeat that kept going would land here
        self.assertEqual([e["event"] for e in self.events][-1], "completed")
        self._assert_heartbeat_stopped()

    def test_crashed_scan_stops_heartbeats_before_reporting_failed(self):
        def detect_then_crash(*args, **kwargs):
            time.sleep(0.3)
            raise RuntimeError("detector blew up")  # not caught per user: main's except reports it

        code = self._run_main(detect_then_crash)

        self.assertEqual(code, 1)
        time.sleep(0.2)  # a heartbeat that kept going would land here
        names = [e["event"] for e in self.events]
        self.assertGreaterEqual(names.count("in_progress"), 2, names)
        self.assertEqual(names[-1], "failed", names)
        self.assertNotIn("completed", names)
        self._assert_heartbeat_stopped()

    def test_heartbeat_on_the_wire_does_not_delay_the_failed_report(self):
        # The failed report runs on a bounded budget before the process exits; a
        # heartbeat send still in flight must not spend it.
        release = threading.Event()
        crashed_at, failed_at = [], []

        def send(*args, **kwargs):
            self._record(*args, **kwargs)
            if args[4] == "failed":
                failed_at.append(time.monotonic())
            elif args[4] == "in_progress" and threading.current_thread().name == "scan-heartbeat":
                release.wait(3)  # a slow backend holding this heartbeat
            return (True, None)

        def detect_then_crash(*args, **kwargs):
            time.sleep(0.3)
            crashed_at.append(time.monotonic())
            raise RuntimeError("detector blew up")

        try:
            code = self._run_main(detect_then_crash, send=send)
        finally:
            release.set()

        self.assertEqual(code, 1)
        self.assertEqual(len(failed_at), 1, self.events)
        self.assertLess(failed_at[0] - crashed_at[0], 1.0, "the failed report waited on the heartbeat")

    def test_heartbeat_send_failures_never_reach_sentry(self):
        # report_to_sentry keeps unlocked per-run state and is main-thread only,
        # so a failing heartbeat must not reach it. Real send_scan_event; curl is
        # faked at subprocess.run, and both hosts are fake so nothing leaves.
        sentry_threads = []
        real_send = utils_mod.send_scan_event

        def fake_run(argv, *args, **kwargs):
            on_heartbeat = threading.current_thread().name == "scan-heartbeat"
            if any("sentry.example.test" in str(a) for a in argv):
                sentry_threads.append(threading.current_thread().name)
                return subprocess.CompletedProcess(argv, 0, stdout="200", stderr="")
            return subprocess.CompletedProcess(
                argv, 0, stdout="{}\n1 " + ("400" if on_heartbeat else "200"), stderr="")

        def send(*args, **kwargs):
            self._record(*args, **kwargs)
            return real_send(*args, **kwargs)

        utils_mod.reset_sentry_run_state()
        with patch.object(utils_mod.subprocess, "run", side_effect=fake_run):
            code = self._run_main(self._slow_detect, send=send,
                                  sentry_dsn="https://key@sentry.example.test/1",
                                  domain="https://backend.example.test")

        self.assertIn(code, (0, None))
        self.assertGreaterEqual([e["event"] for e in self.events].count("in_progress"), 3, self.events)
        self.assertNotIn("scan-heartbeat", sentry_threads)

    def test_failing_heartbeat_send_never_breaks_the_scan(self):
        def send(*args, **kwargs):
            self._record(*args, **kwargs)
            if args[4] == "in_progress" and len(self.events) > 1:
                raise RuntimeError("curl exploded")
            return (True, None)

        code = self._run_main(self._slow_detect, send=send)

        self.assertIn(code, (0, None))
        self.assertEqual(self.events[-1]["event"], "completed")
        self._assert_heartbeat_stopped()

    def test_heartbeats_never_outlast_the_runs_send_deadline(self):
        # --timeout 8 keeps 2 s back for the last sends: the run's send deadline is 6 s in, sooner than
        # a heartbeat's own 10 s budget, so every heartbeat must be clipped to it.
        deadlines = []

        def send(*args, **kwargs):
            deadlines.append((args[4], kwargs.get("deadline")))
            return self._record(*args, **kwargs)

        code = self._run_main(self._slow_detect, send, "--timeout", "8")

        self.assertIn(code, (0, None))
        start = deadlines[0][1]
        self.assertIsNotNone(start)
        beats = [d for event, d in deadlines[1:] if event == "in_progress"]
        self.assertGreaterEqual(len(beats), 2, deadlines)
        self.assertTrue(all(d <= start for d in beats), f"a heartbeat outran the send deadline: {deadlines}")
        self._assert_heartbeat_stopped()


class _HungBackend:
    """Accepts connections and never answers: a backend that stalls mid-request."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d" % self.sock.getsockname()[1]
        self.accepted, self.held = [], []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.held.append(conn)
            self.accepted.append(time.monotonic())

    def close(self):
        self.sock.close()
        for conn in self.held:
            conn.close()


class TestHeartbeatSendBudget(unittest.TestCase):
    """The heartbeat's sends are real curl requests here; only the backend misbehaves."""

    def setUp(self):
        import scripts.coding_discovery_tools.ai_tools_discovery as adm
        self.adm = adm
        self.backend = _HungBackend()
        self.addCleanup(self.backend.close)
        for p in (patch.object(utils_mod, "_SENTRY_DSN", ""),
                  patch.object(utils_mod, "report_to_sentry", Mock())):
            p.start()
            self.addCleanup(p.stop)

    def _send_in_progress(self, deadline):
        return utils_mod.send_scan_event(self.backend.url, "k", "dev", "run-1", "in_progress", deadline=deadline)

    def test_a_send_past_its_deadline_never_reaches_the_backend(self):
        began = time.monotonic()
        success, _ = self._send_in_progress(time.monotonic() - 1)
        self.assertFalse(success)
        self.assertLess(time.monotonic() - began, 1.0)
        time.sleep(0.2)
        self.assertEqual(self.backend.accepted, [])

    def test_stopping_during_a_hung_send_waits_at_most_one_send_budget(self):
        with patch.object(self.adm, "SCAN_HEARTBEAT_INTERVAL_SECONDS", 0.05):
            heartbeat = self.adm._start_scan_heartbeat(self._send_in_progress)
            waited = time.monotonic() + 5
            while not self.backend.accepted and time.monotonic() < waited:
                time.sleep(0.02)
            self.assertTrue(self.backend.accepted, "the heartbeat never sent")
            began = time.monotonic()
            self.adm._stop_scan_heartbeat(heartbeat)
            stopped_at = time.monotonic()
        budget = self.adm.SCAN_EVENT_SEND_SECONDS
        self.assertLessEqual(stopped_at - began, budget + 1)
        # Past its budget the send gives up, so nothing of the heartbeat outlives the run's next step.
        heartbeat[1].join(timeout=2)
        self.assertFalse(heartbeat[1].is_alive(), "a heartbeat send outlived its budget")
        self.assertEqual([t for t in self.backend.accepted if t > stopped_at], [],
                         "a heartbeat request started after the stop")


if __name__ == "__main__":
    unittest.main()
