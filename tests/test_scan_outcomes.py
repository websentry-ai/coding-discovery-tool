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

    def _run_main(self, detect, send=None):
        adm = self.adm
        detector = Mock()
        detector.get_device_id.return_value = "dev"
        detector.detect_all_tools.side_effect = detect
        argv = ["ai_tools_discovery.py", "--api-key", "k", "--domain", "http://127.0.0.1:1"]
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
            patch.object(utils_mod, "_SENTRY_DSN", ""),
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


if __name__ == "__main__":
    unittest.main()
