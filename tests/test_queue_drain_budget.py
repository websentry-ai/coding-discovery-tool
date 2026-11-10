"""The queue drain cannot starve detection, outlive the run budget, or re-send
what it already delivered.

A device that once could not reach the backend keeps its reports in a queue,
which the next run drains before it detects anything. That drain used to be
unbounded: a five-attempt retry cycle with 15/30/60/120s backoff per
unreachable report, so a full queue cost more than the whole run budget. The
run aborted on its own timeout having detected nothing, and queued its own
report on the way out -- a backlog that feeds itself.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.ai_tools_discovery as adm
import scripts.coding_discovery_tools.s3_uploader as s3_mod
from scripts.coding_discovery_tools.s3_uploader import compute_payload_hash
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    MAX_QUEUE_SIZE,
    load_pending_reports,
    save_failed_reports,
    send_report_to_backend,
)

class _SimulatedClock:
    """A stand-in for the `time` module whose sleeps cost no real time but do
    move its clock. Retry backoff stays mocked, as the repository requires, and
    a time budget can still be spent and seen to be spent."""

    def __init__(self):
        self.slept = 0.0

    def sleep(self, seconds):
        self.slept += seconds

    def monotonic(self):
        return time.monotonic() + self.slept

    def __getattr__(self, name):          # everything else is the real module
        return getattr(time, name)


SENT_OK = (True, False)
FAILED_RETRYABLE = (False, True)
FAILED_PERMANENT = (False, False)


def _queued(count):
    """Queued reports, each identifiable so a test can say which were tried."""
    return [
        {"device_id": f"QUEUED-{i}", "home_user": f"user{i}", "tools": []}
        for i in range(count)
    ]


class _Backend:
    """A localhost backend the drain tests drive main() against. Real HTTP, real
    curl, real retry loop -- `rule` only decides what status each data report
    gets back, so a test can say "this one is unreachable" without replacing the
    transport underneath it.

    Threaded on purpose: a stalled handler must not hold up the request behind
    it. Single-threaded, a test that stalls one send makes the next one time out,
    and then passes only because the server drained its backlog before the test
    looked -- which is a race, not the property it meant to assert."""

    def __init__(self, rule=None, stall=0, stall_when=None):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {}
                code = outer._record(self.path, body)
                if code is None:
                    # Hang up without answering: curl reports an empty reply, which
                    # is a send that may well have landed.
                    self.connection.close()
                    return
                if outer.stall and outer._stall_when(body):
                    threading.Event().wait(outer.stall)
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}' if code < 300 else b'{"error":"no"}')

            def log_message(self, *_a):
                pass

        self._rule = rule or (lambda _body: 200)
        self.stall = stall
        self._stall_when = stall_when or (lambda _body: True)
        self._lock = threading.Lock()
        self.received = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def _record(self, path, body):
        # The S3 presign path is refused so every report falls back to the legacy
        # endpoint, which is the one whose retry curve these tests are about.
        if "upload-url" in path or "from-s3" in path:
            return 500
        with self._lock:
            self.received.append(body)
        return self._rule(body)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def device_ids(self, prefix="QUEUED-"):
        """Data reports received, in arrival order, deduped to first attempt. The
        prefix keeps the run's own lifecycle and metrics payloads out of it."""
        with self._lock:
            seen, out = set(), []
            for body in self.received:
                dev = body.get("device_id")
                if body.get("scan_event") or not isinstance(dev, str):
                    continue
                if prefix is not None and not dev.startswith(prefix):
                    continue
                if dev not in seen:
                    seen.add(dev)
                    out.append(dev)
            return out

    def attempts(self, device_id):
        with self._lock:
            return sum(1 for b in self.received if b.get("device_id") == device_id)

    def events(self):
        with self._lock:
            return [b.get("scan_event") for b in self.received if b.get("scan_event")]


def _capturing_timer(armed):
    """Stands in for the watchdog's timer, handing the test its callback instead
    of racing it."""
    class _Captured:
        def __init__(_self, interval, callback):
            armed["interval"] = interval
            armed["fire"] = callback
        def start(_self):
            pass
        def cancel(_self):
            armed["cancelled"] = True
    return patch.object(adm.threading, "Timer", _Captured)


def _posted_device(cmd):
    """The device_id in the body a curl command is posting, or None."""
    try:
        data = cmd[cmd.index("-d") + 1]
        if not data.startswith("@"):
            return None
        return json.loads(Path(data[1:]).read_text()).get("device_id")
    except (ValueError, IndexError, OSError, json.JSONDecodeError):
        return None


def _posted_event(cmd):
    """The scan_event in the body a curl command is posting, or None. The payload
    goes out as a file, so the command names it rather than carrying it."""
    try:
        data = cmd[cmd.index("-d") + 1]
        if not data.startswith("@"):
            return None
        return json.loads(Path(data[1:]).read_text()).get("scan_event")
    except (ValueError, IndexError, OSError, json.JSONDecodeError):
        return None


def statuses(table, default=200):
    """A rule mapping device id to HTTP status: 500 is retryable, 422 is not."""
    return lambda body: table.get(body.get("device_id"), default)


def lost_then_refused():
    """The first answer is lost and the second is a permanent refusal -- the shape
    of an upload that may have landed and will never be accepted again. 422 and
    not 413: a body too large for the legacy endpoint is kept for the S3 route to
    carry next run, so it is not a permanent refusal at all."""
    seen = {"n": 0}

    def _rule(body):
        if not (body.get("device_id") or "").startswith("QUEUED-"):
            return 200
        seen["n"] += 1
        return None if seen["n"] == 1 else 422
    return _rule


def status_for(ok=(), retryable=(), permanent=()):
    """A rule mapping device ids to HTTP statuses: 500 is retryable, 422 is not."""
    def _rule(body):
        dev = body.get("device_id")
        if not retryable and not permanent and not ok and isinstance(dev, str) \
                and dev.startswith("QUEUED-"):
            return 500          # no rule given: the whole backlog is unreachable
        if dev in retryable:
            return 500
        if dev in permanent:
            return 422
        if ok and isinstance(dev, str) and dev.startswith("QUEUED-") and dev not in ok:
            return 500
        return 200
    return _rule


class _DrivesMain:
    """Runs main() against a real localhost backend and a real queue file, and
    reports what the drain actually did: which reports reached the backend, and
    what was left on disk. Only detection, the lock and the cache are stubbed --
    the transport is not."""

    def setUp(self):
        self._prior_queue = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-test-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()
        # The cache and lock live under HOME, but cache.py resolves that ONCE at
        # import time into _HOME_STATE_DIR -- so setting os.environ["HOME"] here
        # redirects nothing and a test using the real cache writes to the
        # developer's own ~/.unbound. Patch the constant, not the environment.
        self._prior_home = (os.environ.get("HOME"), os.environ.get("USERPROFILE"))
        os.environ["HOME"] = self._dir
        os.environ["USERPROFILE"] = self._dir
        state_dir = Path(self._dir) / ".unbound"
        state_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (
            ("_HOME_STATE_DIR", state_dir),
            ("UNBOUND_DIR", state_dir),
            ("CACHE_PATH", state_dir / "discovery-cache.json"),
            ("LOCK_PATH", state_dir / "discovery.lock"),
        ):
            if hasattr(adm.discovery_cache, name):
                pt = patch.object(adm.discovery_cache, name, value)
                pt.start()
                self.addCleanup(pt.stop)
        utils_mod._SENTRY_DSN = ""

    def tearDown(self):
        if self._prior_queue is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior_queue
        for name, value in zip(("HOME", "USERPROFILE"), self._prior_home):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self._dir, ignore_errors=True)

    def seed_queue(self, reports, queued_at=None):
        stamp = queued_at or datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": stamp} for r in reports]
        ))

    def queue_now(self):
        if not self.queue_file.exists():
            return []
        return [e["report"].get("device_id")
                for e in json.loads(self.queue_file.read_text())]

    def run_main(self, rule=None, timeout="12000", patches=(), tools=(), users=(),
                 fresh_report=None, backend=None, stall=0, stall_when=None,
                 domain=None):
        detected = []
        owns_backend = backend is None
        backend = backend or _Backend(rule, stall=stall, stall_when=stall_when)

        def _detect(*_args, **_kwargs):
            detected.append(True)
            return [dict(t) for t in tools]

        detector = Mock()
        detector.get_device_id.return_value = "test-device"
        detector.detect_all_tools.side_effect = _detect
        detector.skills_metrics = {}
        detector.process_single_tool.side_effect = lambda t: dict(t)
        detector.filter_tool_projects_by_user.side_effect = (
            lambda t, _home: {**t, "projects": []}
        )
        if fresh_report is not None:
            detector.generate_single_tool_report.side_effect = (
                fresh_report if callable(fresh_report)
                else (lambda *_a, **_k: dict(fresh_report))
            )

        argv = [
            "ai_tools_discovery.py", "--api-key", "test-key-000000",
            "--domain", domain or backend.url, "--timeout", timeout,
        ]
        try:
            with ExitStack() as stack:
                for ctx in (
                    # Retry sleeps are mocked as the repository requires; the real
                    # curl calls underneath them are not.
                    patch("time.sleep", Mock()),
                    patch.object(adm.os, "_exit", Mock(side_effect=SystemExit(1))),
                    patch.object(adm.platform, "system", return_value="Linux"),
                    patch.object(adm.discovery_cache, "acquire_lock", return_value="acquired"),
                    patch.object(adm.discovery_cache, "heartbeat_start",
                                 Mock(return_value=threading.Event())),
                    patch.object(adm.discovery_cache, "release_lock", Mock()),
                    patch.object(adm.discovery_cache, "resumable_done", return_value=set()),
                    patch.object(adm.discovery_cache, "start_run", Mock()),
                    patch.object(adm.discovery_cache, "mark_run_completed", Mock()),
                    patch.object(adm.discovery_cache, "read_run", return_value={}),
                    patch.object(adm, "AIToolsDetector", return_value=detector),
                    patch.object(adm, "get_all_users_linux", return_value=list(users)),
                    patch.object(adm, "get_user_info", return_value={}),
                    patch.object(adm, "get_audit_user", Mock(return_value=None)),
                    patch.object(adm, "report_to_sentry", Mock()),
                    patch.object(utils_mod, "_SENTRY_DSN", ""),
                    patch.object(sys, "argv", argv),
                ) + tuple(patches):
                    stack.enter_context(ctx)
                try:
                    adm.main()
                except SystemExit:
                    pass
        finally:
            if owns_backend:
                backend.close()
        return backend, bool(detected)


class TheDrainCannotStarveDetection(_DrivesMain, unittest.TestCase):
    """The drain runs ahead of this run's own sends, so what has to hold is that
    it can never stop detection from happening."""

    def test_a_full_queue_still_gets_drained_and_detection_still_runs(self):
        self.seed_queue(_queued(3))

        backend, detected = self.run_main()

        self.assertEqual(backend.device_ids(), ["QUEUED-0", "QUEUED-1", "QUEUED-2"])
        self.assertTrue(detected, "detection never ran")
        self.assertFalse(
            self.queue_file.exists(),
            "the queue file survived a run that owed nothing",
        )

    def test_an_undrainable_queue_does_not_cost_the_run(self):
        """What timed out in production: a full queue, none of it sendable. The
        cost is what matters, and nearly all of it was retry backoff -- five
        attempts per report with 15/30/60/120s waits, about 169s each, so 71
        reports were the whole 12000s run.

        The clock the run reads is advanced by its own mocked sleeps, so that cost
        is spent and measured without spending any real time. Reports are still
        attempted once the backoff budget is gone, which is cheap and deliberate:
        every report gets a try, and none is dropped."""
        pending = _queued(100)
        self.seed_queue(pending)
        clock = _SimulatedClock()

        backend, detected = self.run_main(
            rule=status_for(),
            patches=(
                patch.object(adm, "time", clock),
                patch.object(utils_mod, "time", clock),
            ),
        )

        self.assertLessEqual(
            clock.slept, adm.DRAIN_BUDGET_SECONDS + 1,
            "the drain spent longer than its budget on a queue it could not"
            " send, which is what left the run no time to detect anything",
        )
        self.assertTrue(detected, "the run never reached detection")
        self.assertEqual(
            len(self.queue_now()), len(pending),
            "a report was dropped rather than left for the next run",
        )


class AnUnreachableBackendStopsTheDrain(_DrivesMain, unittest.TestCase):
    """A report that never reached the backend settles it for the whole queue:
    every report behind it would pay its own attempt to learn the same thing.
    This is the one failure that says anything about the reports behind it -- an
    answered one is about its own payload, so it does not stop the drain."""

    def test_a_host_that_was_never_there_leaves_the_rest_untried(self):
        pending = _queued(5)
        self.seed_queue(pending)
        tried = []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list):
                device = _posted_device(cmd)
                if isinstance(device, str) and device.startswith("QUEUED-"):
                    tried.append(device)
            return real_run(cmd, *a, **kw)

        # Nothing listens, so every attempt is refused without connecting.
        self.run_main(
            domain="http://127.0.0.1:1",
            patches=(patch.object(utils_mod.subprocess, "run", _spy),),
        )

        self.assertEqual(
            sorted(set(tried)), ["QUEUED-0"],
            "the queue behind the first unreachable report was tried anyway,"
            " each one paying an attempt to fail the same way",
        )
        self.assertEqual(self.queue_now(), [r["device_id"] for r in pending])

    def test_each_answered_failure_really_paid_its_retry_cycle(self):
        """Through the real transport: a report the backend answers badly costs
        five curl calls, which is the cost the drain budget exists to bound."""
        self.seed_queue(_queued(5))

        backend, _ = self.run_main(rule=status_for())

        self.assertEqual(backend.attempts("QUEUED-0"), utils_mod.MAX_ATTEMPTS)

    def test_a_failure_part_way_through_requeues_only_what_is_left(self):
        self.seed_queue(_queued(5))

        backend, _ = self.run_main(rule=status_for(ok={"QUEUED-0", "QUEUED-1"}))

        self.assertEqual(
            backend.device_ids(),
            ["QUEUED-0", "QUEUED-1", "QUEUED-2", "QUEUED-3", "QUEUED-4"],
        )
        self.assertEqual(
            self.queue_now(), ["QUEUED-2", "QUEUED-3", "QUEUED-4"],
            "delivered reports were re-queued, or a failed one was dropped",
        )

    def test_one_report_that_keeps_failing_does_not_block_the_queue(self):
        """A lone retryable failure is about that payload. Stopping on it would
        strand every healthy report behind it until the queue expired."""
        pending = _queued(5)
        self.seed_queue(pending)

        backend, _ = self.run_main(rule=status_for(retryable={"QUEUED-2"}))

        self.assertEqual(
            backend.device_ids(), [r["device_id"] for r in pending],
            "the drain stopped early",
        )
        self.assertEqual(self.queue_now(), ["QUEUED-2"])

    def test_a_permanent_failure_does_not_stop_the_drain(self):
        """The counterweight: a 4xx is about one payload, so the queue behind it
        is still worth draining."""
        pending = _queued(3)
        self.seed_queue(pending)

        backend, _ = self.run_main(
            rule=status_for(permanent={"QUEUED-0", "QUEUED-1", "QUEUED-2"})
        )

        self.assertEqual(backend.device_ids(), [r["device_id"] for r in pending])
        self.assertEqual(backend.attempts("QUEUED-0"), 1, "a 4xx was retried")
        self.assertEqual(self.queue_now(), [], "a rejected report was re-queued")


class TheDrainIsBoundedByTheRunBudget(_DrivesMain, unittest.TestCase):

    def test_the_ceiling_applies_when_the_run_has_room(self):
        self.assertEqual(
            adm._drain_budget_seconds(12000, 10), float(adm.DRAIN_BUDGET_SECONDS)
        )

    def test_the_budget_is_half_of_what_a_short_run_has_left(self):
        # Detection is what the run is for, so the drain never takes more than half.
        self.assertEqual(adm._drain_budget_seconds(300, 100), 100.0)

    def test_the_budget_is_nothing_once_the_run_is_out(self):
        for timeout, elapsed in ((100, 100), (100, 250)):
            with self.subTest(timeout=timeout, elapsed=elapsed):
                self.assertEqual(adm._drain_budget_seconds(timeout, elapsed), 0.0)

    def test_a_disabled_timeout_still_honours_the_ceiling(self):
        for disabled in (0, -1, None):
            with self.subTest(timeout=disabled):
                self.assertEqual(
                    adm._drain_budget_seconds(disabled, 10**9),
                    float(adm.DRAIN_BUDGET_SECONDS),
                )

    def test_a_spent_budget_leaves_the_whole_queue_untried(self):
        pending = _queued(3)
        self.seed_queue(pending)

        backend, detected = self.run_main(
            patches=(patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),),
        )

        self.assertEqual(backend.device_ids(), [], "the drain ran with no budget left")
        self.assertTrue(detected, "detection was skipped too")
        self.assertEqual(self.queue_now(), [r["device_id"] for r in pending])


class AReportedRunStaysReported(_DrivesMain, unittest.TestCase):
    """The connector sweep runs after the completed event. Two things have to
    hold at once there: the backend must not then be told the run failed, and the
    run must still be stoppable -- a signal that is swallowed leaves only SIGKILL,
    which leaves the lock behind for every later run to trip over."""

    def _abort_during_sweep(self):
        """Runs main() and fires the watchdog from inside the sweep, reporting
        what the backend heard and whether the abort actually ended the run."""
        armed, exited = {}, Mock()

        def _fire_during_sweep(*_a, **_kw):
            armed["fired"] = True
            armed["fire"]()
            return (0, 0, 0)

        backend, _ = self.run_main(
            timeout="900",
            patches=(
                _capturing_timer(armed),
                patch.object(adm, "run_sweep", _fire_during_sweep),
                # A plain stand-in: the real one ends the process rather than
                # raising, and raising here would only surface on the timer's
                # thread as an unhandled exception.
                patch.object(adm.os, "_exit", exited),
            ),
        )
        self.assertTrue(armed.get("fired"), "the sweep never ran, so nothing was tested")
        return backend, exited

    def test_an_abort_in_the_sweep_does_not_undo_the_completed_event(self):
        backend, _ = self._abort_during_sweep()

        self.assertIn("completed", backend.events())
        self.assertNotIn(
            "failed", backend.events(),
            "the backend was told the run completed and then that it failed, so a"
            " scan that did all its work is recorded as a timeout",
        )

    def test_an_undelivered_outcome_does_not_silence_the_failure(self):
        """The completed event can fail like any other send. Having started it is
        not having delivered it: the backend still holds the run in progress, and
        the failure report is the only thing that will ever correct that.

        Both shapes of refusal -- a 500 retried to exhaustion, and a 422 refused
        outright on the first attempt."""
        for code in (500, 422):
            with self.subTest(completed_answer=code):
                armed = {}

                def _fire_during_sweep(*_a, **_kw):
                    armed["fired"] = True
                    armed["fire"]()
                    return (0, 0, 0)

                backend, _ = self.run_main(
                    timeout="900",
                    rule=lambda body, c=code: (
                        c if body.get("scan_event") == "completed" else 200
                    ),
                    patches=(
                        _capturing_timer(armed),
                        patch.object(adm, "run_sweep", _fire_during_sweep),
                        patch.object(adm.os, "_exit", Mock()),
                    ),
                )

                self.assertTrue(armed.get("fired"),
                                "the sweep never ran, so nothing was tested")
                self.assertIn(
                    "failed", backend.events(),
                    "the completed event was refused and the failure suppressed"
                    " anyway, so the backend holds a run that never ends",
                )

    def test_a_completion_that_may_have_landed_silences_the_failure(self):
        """The counterweight to the test above. A completed event whose answer
        was lost may well have been accepted -- the send path's own rule -- so a
        failure afterwards would contradict a run the backend already has."""
        armed = {}

        def _fire_during_sweep(*_a, **_kw):
            armed["fired"] = True
            armed["fire"]()
            return (0, 0, 0)

        backend, _ = self.run_main(
            timeout="900",
            # Hangs up on the completed event without answering, which is what a
            # lost answer looks like from the sending end.
            rule=lambda body: None if body.get("scan_event") == "completed" else 200,
            patches=(
                _capturing_timer(armed),
                patch.object(adm, "run_sweep", _fire_during_sweep),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "the sweep never ran, so nothing was tested")
        self.assertNotIn(
            "failed", backend.events(),
            "the completed event may already have been accepted, so reporting a"
            " failure over it contradicts a run the backend has",
        )

    def test_an_abort_during_the_checkpoint_write_does_not_report_failure(self):
        """The window between the completed event landing and the local
        checkpoint being flipped. That write reads the cache and can report a
        read failure to Sentry, so it is not instant -- and the backend already
        has the run by then."""
        armed = {}

        def _fire_during_checkpoint(*_a, **_kw):
            armed["fired"] = True
            armed["fire"]()

        backend, _ = self.run_main(
            timeout="900",
            patches=(
                _capturing_timer(armed),
                patch.object(adm.discovery_cache, "mark_run_completed",
                             _fire_during_checkpoint),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "the checkpoint never ran")
        self.assertIn("completed", backend.events())
        self.assertNotIn(
            "failed", backend.events(),
            "the completed event was already with the backend, so reporting a"
            " failure during the checkpoint write contradicts it",
        )

    def test_a_crash_after_the_outcome_landed_does_not_report_failure(self):
        """The crash path reported a failure without looking at what the backend
        had already been told."""
        def _boom(*_a, **_kw):
            raise RuntimeError("checkpoint exploded")

        backend, _ = self.run_main(
            timeout="900",
            patches=(patch.object(adm.discovery_cache, "mark_run_completed", _boom),),
        )

        self.assertIn("completed", backend.events())
        self.assertNotIn(
            "failed", backend.events(),
            "a crash after the run was reported complete told the backend it"
            " failed as well",
        )

    def test_the_failure_report_is_bounded_by_the_wait_it_is_given(self):
        """This is the one send that closes a run the backend does not have, and
        it runs on a thread the abort waits a bounded time for. Unbounded, each
        attempt takes the full 60s request timeout and the first backoff sleeps
        7.5-15s, so the process exits mid-sleep and a refused first attempt is
        all a timed-out run ever sends.

        Asserted on what curl is handed rather than on how many attempts happen:
        with sleeps mocked every attempt runs either way, so a count proves
        nothing about the bound."""
        armed, given = {}, []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd \
                    and _posted_event(cmd) == "failed":
                given.append(int(cmd[cmd.index("--max-time") + 1]))
            return real_run(cmd, *a, **kw)

        def _fire_mid_run(*_a, **_kw):
            armed["fired"] = True
            armed["fire"]()
            return ["alice"]

        self.run_main(
            timeout="900",
            patches=(
                _capturing_timer(armed),
                patch.object(adm, "get_all_users_linux", _fire_mid_run),
                patch.object(utils_mod.subprocess, "run", _spy),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "no abort was triggered")
        self.assertTrue(given, "the aborted run sent no failure report")
        self.assertLessEqual(
            max(given), 15,
            f"an attempt was given {max(given)}s inside a 15s bound, so the"
            " process exits before the send or its retry can finish",
        )

    def test_an_abort_while_the_completion_is_in_flight_stays_quiet(self):
        """The request has left, so the backend may have taken it already, and an
        abort can arrive before the send returns to say either way."""
        armed = {}

        def _fire_mid_send(body):
            if body.get("scan_event") == "completed" and "fire" in armed:
                armed["fired"] = True
                armed["fire"]()          # the watchdog, while this send is open
            return 200

        backend, _ = self.run_main(
            timeout="900",
            rule=_fire_mid_send,
            patches=(
                _capturing_timer(armed),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "the completed send never opened")
        self.assertNotIn(
            "failed", backend.events(),
            "the completed request was already with the backend when the abort"
            " fired, and it was told the run failed as well",
        )

    def test_an_abort_between_failed_completion_attempts_reports(self):
        """The counterweight to the two above. Between attempts there is nothing
        on the wire, and the last one never reached the backend -- so the run has
        no terminal outcome anywhere and this report is the only thing that will
        give it one. Suppressing here leaves the scan in progress."""
        armed = {}
        real_backoff = utils_mod._backoff

        def _fire_between_attempts(attempt, deadline=None):
            # Entered after an attempt resolved and before the wait, which is
            # exactly where an abort finds no request on the wire.
            if "fire" in armed and not armed.get("fired"):
                armed["fired"] = True
                armed["fire"]()
            return real_backoff(attempt, deadline)

        backend, _ = self.run_main(
            timeout="900",
            # Hangs up on the completed event, read as never having connected, so
            # every attempt resolves with the backend holding nothing.
            rule=lambda body: None if body.get("scan_event") == "completed" else 200,
            patches=(
                _capturing_timer(armed),
                patch.object(utils_mod, "_backoff", _fire_between_attempts),
                patch.object(utils_mod, "never_connected",
                             side_effect=lambda rc, c: True),
                patch.object(adm.os, "_exit", Mock()),
                patch("time.sleep", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "no abort fired between attempts")
        # The post-send path reports this scenario too, as CompletedEventUndelivered,
        # so the error type is the only thing that shows the abort itself spoke --
        # and it goes first, which is what makes the run's outcome the abort's.
        reported = [b.get("scan_error", {}).get("error_type")
                    for b in backend.received if b.get("scan_event") == "failed"]
        self.assertEqual(
            reported[:1], ["Timeout"],
            "the abort found nothing on the wire and nothing delivered, and still"
            f" said nothing; the run's outcome was reported as {reported}",
        )

    def test_an_abort_before_the_completion_leaves_the_wire_reports(self):
        """The window between claiming the completed send and putting anything on
        the wire: building the payload, serialising it, opening the temp file. An
        abort there has nothing recorded to read -- no request in flight and no
        attempt resolved -- and nothing has reached the backend, so the failure
        report is the only thing that will ever close the run.

        Fired from the serialisation of the completed payload itself, which is in
        that window and is the one send this is about -- keyed on the payload so it
        cannot land on the in_progress or metrics send instead."""
        armed = {}
        real_dumps = utils_mod.json.dumps

        def _fire_while_serialising_the_completion(obj, *a, **kw):
            if (isinstance(obj, dict) and obj.get("scan_event") == "completed"
                    and "fire" in armed and not armed.get("fired")):
                armed["fired"] = True
                armed["fire"]()
            return real_dumps(obj, *a, **kw)

        backend, _ = self.run_main(
            timeout="900",
            patches=(
                _capturing_timer(armed),
                patch.object(utils_mod.json, "dumps",
                             _fire_while_serialising_the_completion),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"),
                        "the abort never fired inside the window under test")
        reported = [b.get("scan_error", {}).get("error_type")
                    for b in backend.received if b.get("scan_event") == "failed"]
        self.assertIn(
            "Timeout", reported,
            "the abort read an unsent completion as one the backend might hold,"
            f" so it said nothing and the run has no outcome of its own: {reported}",
        )

    def test_an_abort_during_a_retried_completion_stays_quiet(self):
        """A refused attempt and a retry that is being accepted. Reading the first
        attempt's refusal as the state of the send reports a failure over a
        completion the backend is taking as it is read -- which is why the flag
        is set before the send rather than written as each attempt resolves."""
        armed, seen = {}, {"n": 0}

        def _refuse_then_accept_mid_abort(body):
            if body.get("scan_event") != "completed":
                return 200
            seen["n"] += 1
            if seen["n"] == 1:
                return 500                 # refused: recorded as rejected
            armed["fired"] = True
            armed["fire"]()                # abort while this retry is in flight
            return 200

        backend, _ = self.run_main(
            timeout="900",
            rule=_refuse_then_accept_mid_abort,
            patches=(
                _capturing_timer(armed),
                patch.object(adm.os, "_exit", Mock()),
            ),
        )

        self.assertTrue(armed.get("fired"), "the completed event was never retried")
        self.assertGreaterEqual(seen["n"], 2, "there was no second attempt to be in")
        self.assertNotIn(
            "failed", backend.events(),
            "the earlier refusal was read as the state of a retry that was being"
            " accepted, so the backend was told the run failed as well",
        )

    def test_a_run_whose_completion_never_landed_closes_itself_on_the_way_out(self):
        """No abort here at all -- the run finishes normally. Its completed event
        was refused, so the backend has it in progress, and nothing else will
        correct that: the local checkpoint stops the next run resuming it and the
        watchdog is cancelled on the way out."""
        backend, _ = self.run_main(
            timeout="900",
            rule=lambda body: 422 if body.get("scan_event") == "completed" else 200,
        )

        self.assertIn(
            "failed", backend.events(),
            "the run exited without telling the backend anything, so that run"
            " sits in progress for ever",
        )

    def test_a_run_whose_completion_landed_closes_as_completed(self):
        """The counterweight: nothing is wrong, so nothing extra is said."""
        backend, _ = self.run_main(timeout="900")

        self.assertIn("completed", backend.events())
        self.assertNotIn(
            "failed", backend.events(),
            "a run the backend accepted was also reported as failed",
        )

    def test_an_abort_in_the_sweep_still_ends_the_run(self):
        """Not reporting the failure is one thing; ignoring the abort is another.
        Swallowed, a signal cannot end a sweep at all -- only SIGKILL can, and
        that leaves the lock behind for every later run to trip over."""
        _, exited = self._abort_during_sweep()

        self.assertTrue(
            exited.called,
            "the abort returned without ending the run, so a signal during the"
            " sweep is silently ignored",
        )


class TheWatchdogAbortsARunThatCannotFinish(_DrivesMain, unittest.TestCase):
    """The timer's own path, driven by firing its callback rather than by racing
    it. A real timer cannot be raced deterministically: every bounded step is
    clipped to finish before the watchdog, so whether the run is still working at
    that instant depends on how fast the machine detects -- which is how an
    earlier version of this passed here and went red on CI."""

    def test_the_watchdog_is_armed_for_the_run_s_own_timeout(self):
        armed = {}

        self.run_main(timeout="900", patches=(_capturing_timer(armed),))

        self.assertEqual(armed.get("interval"), 900,
                         "the run would not be aborted at the timeout it was given")
        self.assertTrue(armed.get("cancelled"),
                        "a timer left running outlives the run that armed it")

    def test_the_watchdog_firing_mid_run_reports_the_failure(self):
        """Fired while the run is still enumerating users, which is where a real
        timeout lands: before anything has been reported."""
        armed = {}

        def _fire_then_return(*_a, **_kw):
            armed["fired"] = True
            armed["fire"]()          # the real _on_watchdog_timeout
            return ["alice"]

        backend, _ = self.run_main(
            timeout="900",
            patches=(
                _capturing_timer(armed),
                patch.object(adm, "get_all_users_linux", _fire_then_return),
            ),
        )

        self.assertTrue(armed.get("fired"), "no watchdog was armed to fire")
        self.assertIn("failed", backend.events(),
                      "the aborted run never told the backend it failed")
        self.assertNotIn("completed", backend.events(),
                         "the run carried on and reported success after aborting")


class ADeliveredReportLeavesTheQueue(unittest.TestCase):
    """The queue file is rewritten from what is still owed. Merging onto it left
    every delivered report in place, to be re-sent on every run for 24 hours."""

    def setUp(self):
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-queue-test-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def _seed(self, ids):
        now = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": {"id": i}, "queued_at": now} for i in ids]
        ))

    def _queue_ids(self):
        return [e["report"]["id"] for e in json.loads(self.queue_file.read_text())]

    def test_an_authoritative_write_drops_what_was_delivered(self):
        self._seed(["old-A", "old-B"])
        load_pending_reports()  # both drained successfully

        save_failed_reports([{"id": "today-C"}], replace=True)

        self.assertEqual(self._queue_ids(), ["today-C"])

    def test_the_default_still_merges(self):
        self._seed(["old-A"])

        save_failed_reports([{"id": "today-C"}])

        self.assertEqual(self._queue_ids(), ["old-A", "today-C"])

    def test_an_overflowing_queue_keeps_this_runs_reports(self):
        """main() writes the backlog first, so the cap trims the oldest rather
        than the reports this run just failed to send."""
        backlog = [{"id": f"old-{i}"} for i in range(MAX_QUEUE_SIZE)]
        fresh = [{"id": f"today-{i}"} for i in range(5)]

        save_failed_reports(backlog + fresh, replace=True)

        kept = self._queue_ids()
        self.assertEqual(len(kept), MAX_QUEUE_SIZE)
        for report in fresh:
            self.assertIn(report["id"], kept)


class ARequeuedReportStillExpires(unittest.TestCase):
    """A requeue must not restamp queued_at. Refreshing it on every run would stop
    the 24-hour expiry ever firing, so a report that can never be delivered would
    sit in the queue forever and be retried forever."""

    def setUp(self):
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-age-test-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_a_requeued_report_keeps_its_original_timestamp(self):
        aged = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
        report = {"device_id": "D", "home_user": "u", "tools": []}
        self.queue_file.write_text(json.dumps([{"report": report, "queued_at": aged}]))

        save_failed_reports([report], replace=True)

        [envelope] = json.loads(self.queue_file.read_text())
        self.assertEqual(
            envelope["queued_at"], aged,
            "the requeue reset the clock, so this report can never age out",
        )

    def test_a_report_queued_for_the_first_time_is_stamped_now(self):
        save_failed_reports([{"device_id": "D", "home_user": "u", "tools": []}],
                            replace=True)

        [envelope] = json.loads(self.queue_file.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(envelope["queued_at"])
        self.assertLess(age.total_seconds(), 60)

    def test_an_aged_requeue_is_discarded_on_the_next_load(self):
        """The end of the story: once it is past the window, the next run drops it."""
        aged = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
        report = {"device_id": "D", "home_user": "u", "tools": []}
        self.queue_file.write_text(json.dumps([{"report": report, "queued_at": aged}]))
        save_failed_reports([report], replace=True)

        with patch.object(utils_mod, "QUEUE_MAX_AGE_SECONDS", 22 * 3600):
            self.assertEqual(load_pending_reports(), [])


class AQueuedReportTheBackendHasAlreadyReplacedIsDropped(unittest.TestCase):
    """The drain can stop early, leaving a report queued while this run goes on to
    report that same tool successfully. Sending it next run would replace the fresh
    row, and this run's stored payload hash then stops detection putting it back."""

    def test_a_queued_report_for_a_tool_reported_this_run_is_dropped(self):
        queued = [
            {"home_user": "alice", "tools": [{"name": "Claude Code"}]},
            {"home_user": "bob", "tools": [{"name": "Cursor"}]},
        ]
        kept = adm._drop_superseded(queued, {("alice", "Claude Code")})

        self.assertEqual([r["home_user"] for r in kept], ["bob"])

    def test_the_same_tool_for_another_user_is_kept(self):
        queued = [{"home_user": "bob", "tools": [{"name": "Claude Code"}]}]

        self.assertEqual(adm._drop_superseded(queued, {("alice", "Claude Code")}), queued)

    def test_a_report_whose_tool_cannot_be_identified_is_kept(self):
        """Never drop on a guess: a shape we cannot read is still owed."""
        for report in (
            {"home_user": "alice", "tools": []},
            {"home_user": "alice", "tools": [{"name": "A"}, {"name": "B"}]},
            {"home_user": "alice"},
            {},
        ):
            with self.subTest(report=report):
                self.assertEqual(
                    adm._drop_superseded([report], {("alice", "Claude Code")}), [report]
                )
                self.assertIsNone(adm._report_identity(report))


class _AlwaysFailingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.hits += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.server.delay:
            threading.Event().wait(self.server.delay)
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"down"}')

    def log_message(self, *_a):
        pass


class TheTransportItselfRespectsTheDeadline(unittest.TestCase):
    """A real localhost server and real curl. Checking the budget only between
    reports left one report free to overrun it: five attempts at --max-time 60
    plus ~225s of backoff, which on a short run outlives the run itself."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _AlwaysFailingHandler)
        cls.server.hits = 0
        cls.server.delay = 0
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.server.hits = 0
        self.server.delay = 0
        # These assertions are about the real retry curve, so the class must not
        # inherit backoff tuning left in the environment by another test.
        unturned = {k: v for k, v in os.environ.items()
                    if not k.startswith("AI_DISCOVERY_BACKOFF_")}
        env = patch.dict(os.environ, unturned, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.report = {"device_id": "D", "home_user": "u", "tools": []}
        self.url = f"http://127.0.0.1:{self.port}"

    def test_a_deadline_already_passed_starts_no_request_at_all(self):
        success, retryable = send_report_to_backend(
            self.url, "test-key-000000", self.report, deadline=time.monotonic() - 1,
        )

        self.assertEqual((success, retryable), (False, True))
        self.assertEqual(self.server.hits, 0, "a request went out past the deadline")

    def test_a_near_deadline_stops_after_one_attempt_without_sleeping(self):
        # Comfortable room for one instant request, and none for a backoff: the
        # smallest real wait is 7.5s. Sleeps are mocked so a regression here fails
        # fast instead of serving out the retry curve, and the request count is
        # what proves the guard -- counting sleeps cannot, because subprocess
        # calls time.sleep too while it polls curl.
        with patch("time.sleep"):
            success, retryable = send_report_to_backend(
                self.url, "test-key-000000", self.report,
                deadline=time.monotonic() + 6,
            )

        self.assertEqual((success, retryable), (False, True))
        self.assertEqual(
            self.server.hits, 1,
            "it retried past its deadline; with the backoff refused there is"
            " nothing to wait for and the attempt loop must stop",
        )

    def test_a_request_cannot_outlive_the_remaining_budget(self):
        """Not starting a request past the deadline is not enough: the request's own
        timeout has to be clipped too, or one slow upload outlives a short run."""
        self.server.delay = 8
        started = time.monotonic()

        with patch("time.sleep"):
            success, retryable = send_report_to_backend(
                self.url, "test-key-000000", self.report,
                deadline=time.monotonic() + 2,
            )
        elapsed = time.monotonic() - started

        self.assertEqual((success, retryable), (False, True))
        self.assertLess(
            elapsed, 7,
            "the request kept its own fixed timeout and outlived the deadline",
        )

    def test_without_a_deadline_the_retry_curve_is_unchanged(self):
        """The existing callers pass no deadline and must keep all five attempts."""
        with patch("time.sleep") as slept:
            success, retryable = send_report_to_backend(
                self.url, "test-key-000000", self.report,
            )

        self.assertEqual((success, retryable), (False, True))
        self.assertEqual(self.server.hits, utils_mod.MAX_ATTEMPTS)
        # At least one backoff between each pair of attempts; subprocess polling
        # can add its own sleeps, so this is a floor rather than an equality.
        self.assertGreaterEqual(slept.call_count, utils_mod.MAX_ATTEMPTS - 1)


class TheS3PathIsBoundedByTheSameDeadline(unittest.TestCase):
    """Real queued tool reports take the S3 path first: three requests whose fixed
    30/180/30s limits could each outlast a short run's whole budget."""

    def test_each_step_is_clipped_to_what_is_left(self):
        self.assertEqual(utils_mod.clip_to_deadline(30, None), 30)
        self.assertEqual(utils_mod.clip_to_deadline(30, time.monotonic() + 100), 30)
        self.assertIn(utils_mod.clip_to_deadline(180, time.monotonic() + 10), (9, 10))

    def test_a_passed_deadline_starts_no_step(self):
        self.assertIsNone(utils_mod.clip_to_deadline(30, time.monotonic() - 1))
        self.assertEqual(
            s3_mod.try_s3_upload(
                "http://127.0.0.1:1", "k", {"device_id": "D", "tools": [{"name": "T"}]},
                deadline=time.monotonic() - 1,
            ),
            (False, True),
        )


class ThisRunsOwnReportsAreBoundedToo(_DrivesMain, unittest.TestCase):
    """Capping the drain is only half of it. A backend that is down costs each
    (tool, user) report five attempts and ~169s of backoff as well, so on a
    multi-user device with several tools this run's own sends could spend the
    budget the drain was capped to protect.

    Observed through the real transport: a deadline shows up as attempts the run
    does not make. The backend stalls before answering, so each attempt costs
    real time even with the retry sleeps mocked."""

    ONE_TOOL = ({"name": "Claude Code", "version": "1.0",
                 "install_path": "/usr/local/bin/claude"},)

    def _tool_run(self, timeout, stall=0, rule=None, stall_when=None):
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        return self.run_main(
            rule=rule, timeout=timeout, stall=stall, stall_when=stall_when,
            tools=self.ONE_TOOL, users=("alice",), fresh_report=fresh,
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report", Mock()),
            ),
        )

    def test_a_per_tool_send_stops_retrying_inside_the_run_budget(self):
        """Eight seconds of budget leaves six for the sends, and each attempt costs
        the backend's two-second stall, so the fifth attempt is never started."""
        backend, _ = self._tool_run(
            "8", stall=2, rule=statuses({"THIS-RUN": 500}),
            # Only this run's report stalls, so the lifecycle sends stay quick and
            # the run finishes inside its own budget rather than being aborted.
            stall_when=lambda body: body.get("device_id") == "THIS-RUN",
        )

        tried = backend.attempts("THIS-RUN")
        self.assertGreater(tried, 0, "this run's report never went out")
        self.assertLess(
            tried, utils_mod.MAX_ATTEMPTS,
            "the send used its whole retry curve regardless of the run budget, so a"
            " down backend can still spend the run on this one report",
        )

    def test_a_disabled_run_timeout_leaves_the_retry_curve_whole(self):
        """--timeout <=0 turns the self-deadline off by design; the sends follow."""
        backend, _ = self._tool_run("0", stall=0, rule=statuses({"THIS-RUN": 500}))

        self.assertEqual(backend.attempts("THIS-RUN"), utils_mod.MAX_ATTEMPTS)

    def test_a_short_run_still_has_room_to_upload(self):
        """A fixed reserve bigger than the run leaves the sends no time at all, so
        a short scan would detect everything and report none of it."""
        backend, _ = self._tool_run("300")

        self.assertIn(
            "THIS-RUN", backend.device_ids(prefix="THIS-RUN"),
            "the reserve ate the whole budget, so a short scan uploaded nothing",
        )

    def test_the_completed_event_gets_the_whole_budget(self):
        """It carries the manifest the backend prunes against, so it is the one
        send that must not be skipped. Holding back an end-of-run tail from it
        left a scan that finished inside that tail with no time to send at all.

        Read off the real send: curl is given the smaller of its own timeout and
        what is left of the deadline, so raising that timeout past the budget
        makes the deadline the bound curl is handed."""
        bounds = []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd and _posted_event(cmd) == "completed":
                bounds.append((time.monotonic(), int(cmd[cmd.index("--max-time") + 1])))
            return real_run(cmd, *a, **kw)

        started = time.monotonic()
        self.run_main(
            timeout="900",
            patches=(
                patch.object(utils_mod.subprocess, "run", _spy),
                patch.object(utils_mod, "REQUEST_TIMEOUT_SECONDS", 10_000),
            ),
        )

        self.assertTrue(bounds, "no completed event was sent")
        sent_at, max_time = bounds[0]
        watchdog_at = started + 900
        self.assertAlmostEqual(
            sent_at + max_time, watchdog_at, delta=2,
            msg="the event was handed less than the run had left, which is what"
                " leaves a scan finishing inside the tail unable to send at all",
        )

    def test_a_short_run_still_reports_completion(self):
        """The end-of-run grace is subtracted from the deadline, so a flat one puts
        a short scan's completed event behind its own deadline and the backend
        never hears that the run finished."""
        backend, _ = self.run_main(timeout="20")

        self.assertIn(
            "completed", backend.events(),
            "the run did its work and never reported it: the grace margin did not"
            " scale to the budget",
        )

    def test_metrics_cannot_spend_the_completed_event_s_window(self):
        """Metrics are fire-and-forget; the completed event carries the manifest
        the backend prunes against. Sharing one deadline, a slow metrics POST
        leaves the event's deadline already passed and it never goes.

        The stall has to outlast the whole tail for that to happen, so the budget
        is small and the stall longer than what the tail has left."""
        backend = _Backend(stall=5, stall_when=lambda b: not b.get("scan_event"))

        try:
            self.run_main(timeout="6", backend=backend)
        finally:
            backend.close()

        self.assertIn(
            "completed", backend.events(),
            "the metrics POST spent the tail and the run never reported finishing",
        )

    def test_the_lifecycle_events_stop_retrying_inside_the_budget(self):
        """in_progress and completed carry the manifest and the run's shape. Each
        is a full retry cycle on an unreachable backend if it is not bounded."""
        backend, _ = self.run_main(
            rule=lambda body: 500 if body.get("scan_event") == "in_progress" else 200,
            timeout="8", stall=2,
            stall_when=lambda body: body.get("scan_event") == "in_progress",
        )

        started = [e for e in backend.events() if e == "in_progress"]
        self.assertTrue(started, "no in_progress event was sent")
        self.assertLess(
            len(started), utils_mod.MAX_ATTEMPTS,
            "the in_progress event used its whole retry curve regardless of the"
            " run budget",
        )


class ADeliveredQueuedReportStillGetsCorrected(_DrivesMain, unittest.TestCase):
    """The hard half of the rule. A queued report exists because its upload
    failed, so the cache still holds the hash of an *earlier* upload. Delivering
    it replaces the backend snapshot with that older payload -- and a matching
    hash would then make detection skip the upload that would correct it, for
    good, because the hash keeps matching until something changes on the device.

    The queue having mentioned the pair at all is what settles this: the hash is
    not consulted for it, whether the drain delivered that report or left it
    owed. Nothing is written to the cache, so there is no write that can fail."""

    def test_the_hash_goes_before_the_snapshot_does(self):
        """Clearing after the send leaves a window where the backend already holds
        the older payload and a kill stops the clear. And a report the endpoint
        refuses outright is discarded, so for that one the clear is the only thing
        left that stops detection trusting a row an earlier attempt may have
        replaced over S3."""
        backend = _Backend(statuses({"QUEUED-0": 422}))
        posted_when_cleared = []
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        def _spy(tool, user):
            posted_when_cleared.append((user, tool, len(backend.received)))
            return True

        try:
            self.run_main(
                backend=backend,
                patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                      side_effect=_spy),),
            )
        finally:
            backend.close()

        self.assertEqual(
            posted_when_cleared, [("alice", "Claude Code", 0)],
            "the pair's hash was not cleared ahead of everything this run sends,"
            " so a discarded or half-delivered snapshot can leave a row that"
            " detection goes on treating as current",
        )
        self.assertEqual(self.queue_now(), [],
                         "422 is permanent; requeueing it would never end")

    def test_a_clear_that_did_not_land_holds_the_snapshot_back(self):
        """Nothing this run remembers about a failed clear outlives it, so the
        older snapshot must not go out behind a hash that still describes the row
        it would replace. The report stays for a later run to try again."""
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                  return_value=False),),
        )

        self.assertEqual(
            backend.device_ids(), [],
            "it went out behind a hash that still described the row it replaced",
        )
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "the report was dropped although it was never delivered, so nothing"
            " will ever send it again",
        )

    def test_a_delivered_snapshot_cannot_hide_behind_a_matching_hash(self):
        """The state a failed queue write leaves behind: the pair's hash is
        recorded, and the superseded report is still in the queue because the
        write that should have dropped it never landed. This run's drain then
        delivers that older snapshot, so the backend holds it -- and the hash
        matches this run's payload exactly, which is what would suppress the
        upload that corrects it.

        It must not, and what prevents it is the queue naming the pair at all --
        which is true precisely because the write failed."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            tools=tools, users=("alice",), fresh_report=fresh,
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                # The hash production really computes for this payload: a literal
                # would never reach the dedup branch and would prove nothing.
                patch.object(adm.discovery_cache, "get_cached_hash",
                             return_value=compute_payload_hash(fresh["tools"][0])),
            ),
        )

        self.assertEqual(
            backend.device_ids(), ["QUEUED-0"],
            "the drain never delivered it, so no row was replaced and this is not"
            " the state a failed write leaves behind",
        )
        self.assertIn(
            "THIS-RUN", backend.device_ids(prefix="THIS-RUN"),
            "a matching hash suppressed the correcting upload after the drain had"
            " already replaced the row, so the stale snapshot stands",
        )

    def test_the_hash_goes_before_the_snapshot_does(self):
        """Clearing after the send leaves a window where the backend already holds
        the older payload and a kill stops the clear. And a report the endpoint
        refuses outright is discarded, so for that one the clear is the only thing
        left that stops detection trusting a row an earlier attempt may have
        replaced over S3."""
        backend = _Backend(statuses({"QUEUED-0": 422}))
        posted_when_cleared = []
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        def _spy(tool, user):
            posted_when_cleared.append((user, tool, len(backend.received)))
            return True

        try:
            self.run_main(
                backend=backend,
                patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                      side_effect=_spy),),
            )
        finally:
            backend.close()

        self.assertEqual(
            posted_when_cleared, [("alice", "Claude Code", 0)],
            "the pair's hash was not cleared ahead of everything this run sends,"
            " so a discarded or half-delivered snapshot can leave a row that"
            " detection goes on treating as current",
        )
        self.assertEqual(self.queue_now(), [],
                         "422 is permanent; requeueing it would never end")

    def test_a_clear_that_did_not_land_holds_the_snapshot_back(self):
        """Nothing this run remembers about a failed clear outlives it, so the
        older snapshot must not go out behind a hash that still describes the row
        it would replace. The report stays for a later run to try again."""
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                  return_value=False),),
        )

        self.assertEqual(
            backend.device_ids(), [],
            "it went out behind a hash that still described the row it replaced",
        )
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "the report was dropped although it was never delivered, so nothing"
            " will ever send it again",
        )

    def test_the_hash_goes_before_the_snapshot_does(self):
        """Clearing after the send leaves a window where the backend already holds
        the older payload and a kill stops the clear. And a report the endpoint
        refuses outright is discarded, so for that one the clear is the only thing
        left that stops detection trusting a row an earlier attempt may have
        replaced over S3."""
        backend = _Backend(statuses({"QUEUED-0": 422}))
        posted_when_cleared = []
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        def _spy(tool, user):
            posted_when_cleared.append((user, tool, len(backend.received)))
            return True

        try:
            self.run_main(
                backend=backend,
                patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                      side_effect=_spy),),
            )
        finally:
            backend.close()

        self.assertEqual(
            posted_when_cleared, [("alice", "Claude Code", 0)],
            "the pair's hash was not cleared ahead of everything this run sends,"
            " so a discarded or half-delivered snapshot can leave a row that"
            " detection goes on treating as current",
        )
        self.assertEqual(self.queue_now(), [],
                         "422 is permanent; requeueing it would never end")

    def test_a_clear_that_did_not_land_holds_the_snapshot_back(self):
        """Nothing this run remembers about a failed clear outlives it, so the
        older snapshot must not go out behind a hash that still describes the row
        it would replace. The report stays for a later run to try again."""
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                  return_value=False),),
        )

        self.assertEqual(
            backend.device_ids(), [],
            "it went out behind a hash that still described the row it replaced",
        )
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "the report was dropped although it was never delivered, so nothing"
            " will ever send it again",
        )

    def test_the_correcting_upload_actually_goes_out(self):
        """End to end with the real cache: run one uploads and records its hash,
        then run two finds an older report queued for the same pair. The backend
        must end on this run's snapshot, not the one the drain delivered."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        live = (
            patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
            patch.object(adm, "_install_in_another_users_home", return_value=False),
            patch.object(adm, "_machine_global_install_disowned", return_value=False),
            patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
        )

        # Run one: nothing queued, so this just records the pair's hash.
        first, _ = self.run_main(tools=tools, users=("alice",),
                                 fresh_report=fresh, patches=live)
        self.assertIn("THIS-RUN", first.device_ids(prefix="THIS-RUN"))

        # Run two: the same local state, with an older report for that pair queued.
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])
        second, _ = self.run_main(tools=tools, users=("alice",),
                                  fresh_report=fresh, patches=live)

        self.assertIn(
            "QUEUED-0", second.device_ids(), "the drain never delivered the old one"
        )
        self.assertIn(
            "THIS-RUN", second.device_ids(prefix="THIS-RUN"),
            "the stale hash suppressed the correcting upload, so the backend is"
            " left holding the snapshot the drain delivered",
        )

    def test_a_pair_that_errors_after_its_snapshot_landed_loses_its_hash(self):
        """The drain replaces the row with the older payload and this run then
        errors on that pair, so it never gets to correct it -- a per-tool
        permission error is enough. What stops the next run reading a hash match
        and skipping the upload for good is that the hash is already gone from
        disk, which no error later in this run can undo."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            tools=tools, users=("alice",), fresh_report=fresh,
            patches=(
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                # Errors where a real per-tool failure does, inside the try that
                # reports it: this one runs before the upload is even considered.
                patch.object(adm, "_refresh_mcp_tools_cache",
                             side_effect=PermissionError("alice's home is not readable")),
            ),
        )

        self.assertIn("QUEUED-0", backend.device_ids(),
                      "the drain never delivered it, so no row was replaced")
        self.assertEqual(backend.device_ids(prefix="THIS-RUN"), [],
                         "the pair did not error, so this is not the path tested")
        self.assertIsNone(
            adm.discovery_cache.get_cached_hash("Claude Code", "alice"),
            "the pair errored after its older snapshot reached the backend and"
            " its hash survived, so the next run reads a match and never"
            " corrects the row",
        )

    def test_a_delivered_report_is_only_dropped_once_something_replaced_it(self):
        """The drain delivers the older snapshot and the correcting upload then
        fails. The queue has to end up holding the fresh report: dropping the
        delivered one without it would leave the backend on the older snapshot
        with nothing owed for the pair."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            tools=tools, users=("alice",), fresh_report=fresh,
            rule=statuses({"THIS-RUN": 500}),
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
            ),
        )

        self.assertIn("QUEUED-0", backend.device_ids(),
                      "the drain never delivered it, so nothing replaced a row")
        self.assertEqual(
            self.queue_now(), ["THIS-RUN"],
            "the correcting upload failed and nothing is owed for the pair, so"
            " the backend is left on the delivered payload with no correction",
        )

    def test_a_cache_that_cannot_be_read_holds_the_snapshot_back(self):
        """A different failure from an unwritable directory: the file is there and
        readable as nothing. `read_cache` flattens a read error into an empty
        dict, so a corrupt cache looks exactly like a cache with no hash in it --
        and clearing a hash out of a file we cannot parse is not something we can
        claim to have done. The snapshot has to stay put."""
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])
        cache_path = adm.discovery_cache.CACHE_PATH
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("{ this is not json")

        backend, _ = self.run_main()

        self.assertEqual(
            backend.device_ids(), [],
            "the older snapshot replaced the backend's row while the pair's hash"
            " could neither be read nor cleared, so nothing can correct it",
        )
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "it was dropped without being delivered, so nothing will send it"
            " once the cache is readable again",
        )

    @unittest.skipUnless(os.name == "posix", "needs POSIX directory permissions")
    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                     "root writes through the directory mode this relies on")
    def test_an_unwritable_cache_cannot_leave_a_row_nothing_corrects(self):
        """The same scenario with the real cache and a really unwritable state
        directory, which is the one place the hash lives. The rule needs no write
        to hold: run one stores the hash, the directory goes read-only, and run
        two still delivers the queued report and corrects the row behind it."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        live = (
            patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
            patch.object(adm, "_install_in_another_users_home", return_value=False),
            patch.object(adm, "_machine_global_install_disowned", return_value=False),
            patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
        )

        first, _ = self.run_main(tools=tools, users=("alice",),
                                 fresh_report=fresh, patches=live)
        self.assertIn("THIS-RUN", first.device_ids(prefix="THIS-RUN"))
        state_dir = adm.discovery_cache.CACHE_PATH.parent
        self.assertIsNotNone(
            adm.discovery_cache.get_cached_hash("Claude Code", "alice"),
            "run one stored no hash, so there is no stale hash to survive",
        )

        # Readable, not writable: the hash can be read and nothing can change it.
        self.addCleanup(os.chmod, state_dir, 0o700)
        os.chmod(state_dir, 0o500)
        self.seed_queue(
            [{"device_id": "QUEUED-0", "home_user": "alice",
              "tools": [{"name": "Claude Code"}]}],
            queued_at=datetime.now(timezone.utc).isoformat(),
        )

        second, _ = self.run_main(tools=tools, users=("alice",),
                                  fresh_report=fresh, patches=live)

        self.assertEqual(
            adm.discovery_cache.get_cached_hash("Claude Code", "alice"),
            compute_payload_hash(fresh["tools"][0]),
            "the hash this test is about never survived, so it proves nothing",
        )
        self.assertEqual(
            second.device_ids(), [],
            "the older snapshot replaced the row while the hash describing the"
            " newer one was still on disk and could not be removed",
        )
        self.assertIn(
            "THIS-RUN", second.device_ids(prefix="THIS-RUN"),
            "the surviving hash matched the unchanged payload and suppressed this"
            " run's upload too, so nothing corrected the pair at all",
        )
        self.assertEqual(
            self.queue_now(), [],
            "this run reported the pair itself, so the held-back snapshot is"
            " superseded and keeping it would only overwrite that report later",
        )

class ASupersededReportLeavesDiskAtOnce(_DrivesMain, unittest.TestCase):
    """The queue is authoritative for what is still owed, and the run can end at
    any instruction. Between uploading a pair and the persist step at the end of
    the run, a queued report for that pair is an overwrite sitting on disk: the
    next run's drain would deliver it over what this run just sent."""

    TOOLS = ({"name": "Claude Code", "version": "1.0",
              "install_path": "/usr/local/bin/claude"},)
    FRESH = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
             "tools": [{"name": "Claude Code", "projects": []}]}

    def _live(self):
        return (
            patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
            patch.object(adm, "_install_in_another_users_home", return_value=False),
            patch.object(adm, "_machine_global_install_disowned", return_value=False),
            patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
            # Left untried, so it is still owed when detection reaches the pair.
            patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
        )

    def test_the_queue_is_clear_of_the_pair_when_its_hash_is_stored(self):
        """Observed at the instant the checkpoint is written, which is the first
        moment a kill could strand the queue entry."""
        on_disk = {}
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        def _record(*_a, **_k):
            on_disk["queue"] = self.queue_now()

        self.run_main(
            tools=self.TOOLS, users=("alice",), fresh_report=self.FRESH,
            patches=self._live() + (
                patch.object(adm.discovery_cache, "record_report",
                             side_effect=_record),
            ),
        )

        self.assertIn("queue", on_disk, "the pair was never reported at all")
        self.assertNotIn(
            "QUEUED-0", on_disk["queue"],
            "the superseded report was still on disk when this run recorded the"
            " pair, so an interruption there replays it over what was just sent",
        )

    def test_the_untried_tail_is_filtered_before_it_is_requeued(self):
        """An early stop requeues the tail wholesale. A pair the resumed run has
        already reported must not go back on the queue to overwrite it next run."""
        other = {"device_id": "Q-KEEP", "home_user": "bob",
                 "tools": [{"name": "Cursor"}]}
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        # The superseded one sits BEHIND the stop point, so it reaches the queue as
        # part of the untried tail rather than being skipped report by report.
        self.seed_queue([other, superseded])

        at_detection = {}

        def _probe():
            at_detection["queue"] = (
                [e["report"]["device_id"]
                 for e in json.loads(self.queue_file.read_text())]
                if self.queue_file.exists() else []
            )
            return []

        self.run_main(
            patches=(
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
                patch.object(adm, "get_all_users_linux", side_effect=_probe),
                patch.object(adm.discovery_cache, "resumable_done",
                             return_value={("Claude Code:/usr/local/bin/claude", "alice")}),
            ),
        )

        # The filter at persist would catch it, but an abort never reaches persist.
        self.assertEqual(
            at_detection.get("queue"), ["Q-KEEP"],
            "the untried tail went to disk without checking what this run had"
            " already reported, so an abort would leave it there",
        )

    def test_what_the_drain_settled_is_off_disk_before_detection(self):
        """An abort later in the run skips the persist step entirely -- the
        watchdog calls os._exit -- so a report the drain has already delivered has
        to leave the file straight away. Left on it, the next run delivers that
        older snapshot again, over whatever this run uploaded in between."""
        at_detection = {}
        self.seed_queue([{"device_id": "Q-GONE", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        def _probe():
            at_detection["queue"] = self.queue_now()
            return []

        backend, _ = self.run_main(
            patches=(patch.object(adm, "get_all_users_linux", side_effect=_probe),),
        )

        self.assertEqual(backend.device_ids(prefix="Q-"), ["Q-GONE"],
                         "the drain never delivered it, so nothing was settled")
        self.assertEqual(
            at_detection.get("queue"), [],
            "a report the drain had already delivered was still on disk when"
            " detection began, so an abort there sends it a second time",
        )

    def test_a_pair_the_backlog_never_held_costs_no_extra_write(self):
        """The counterweight: rewriting per upload would re-read and re-serialise
        the whole queue for every tool on the device, for nothing. Two writes
        belong to the run itself -- one settling what the drain delivered, one
        persisting what is still owed -- and the pair's upload must add none."""
        writes = []
        real_save = adm.save_failed_reports
        self.seed_queue([{"device_id": "Q-OTHER", "home_user": "bob",
                          "tools": [{"name": "Cursor"}]}])

        def _spy(reports, **kw):
            writes.append(len(reports))
            return real_save(reports, **kw)

        self.run_main(
            tools=self.TOOLS, users=("alice",), fresh_report=self.FRESH,
            patches=self._live() + (
                patch.object(adm, "save_failed_reports", side_effect=_spy),
            ),
        )

        self.assertEqual(
            len(writes), 2,
            "the run's own two writes are the post-drain settle and the final"
            " persist; a third means the queue was rewritten for a pair it never"
            f" held a report for, so every upload pays for it: {writes}",
        )


class TwoInstallsOfOneToolShareOneRow(_DrivesMain, unittest.TestCase):
    """The resume key carries the install path, so two installs of the same tool
    name are separate iterations -- but the backend replaces rows per tool name,
    so they are one row. A failure for one of them must not outlive a success for
    the other: queued, it goes out on the next run and replaces what did land."""

    TOOLS = ({"name": "Claude Code", "version": "1.0",
              "install_path": "/usr/local/bin/claude"},
             {"name": "Claude Code", "version": "1.0",
              "install_path": "/opt/homebrew/bin/claude"})

    def test_a_failure_for_one_install_does_not_outlive_the_other_s_success(self):
        sent = {"n": 0}

        def _report_per_install(*_a, **_k):
            sent["n"] += 1
            return {"home_user": "alice", "system_user": None,
                    "device_id": f"INSTALL-{sent['n']}",
                    "tools": [{"name": "Claude Code", "projects": []}]}

        # The first install's report fails every attempt; the second's lands.
        backend, _ = self.run_main(
            tools=self.TOOLS, users=("alice",), fresh_report=_report_per_install,
            rule=statuses({"INSTALL-1": 500}),
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch("time.sleep", Mock()),
            ),
        )

        self.assertIn("INSTALL-2", backend.device_ids(prefix="INSTALL-2"),
                      "the second install never landed, so nothing superseded the"
                      " first and this is not the case under test")
        self.assertEqual(
            self.queue_now(), [],
            "the failed report for the other install stayed owed after a send for"
            " the same row succeeded, so the next run replaces what landed today"
            " with it",
        )

class AQueuedReportOverridesTheCachedHash(_DrivesMain, unittest.TestCase):
    """The cache says what we last uploaded. A queued older report says the drain
    may since have replaced that row with it -- in this run or a previous one whose
    invalidation never reached disk. The queue wins: detection re-uploads, which
    corrects the row now instead of waiting for a later run to deliver the report.
    And because the upload is what made the row current, it is also what earns the
    right to drop the queued copy."""

    TOOLS = ({"name": "Claude Code", "version": "1.0",
              "install_path": "/usr/local/bin/claude"},)
    FRESH = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
             "tools": [{"name": "Claude Code", "projects": []}]}

    def setUp(self):
        super().setUp()
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])
        # The hash production really computes for this payload: with a literal the
        # dedup branch is never reached and the test proves nothing.
        self.matching = compute_payload_hash(self.FRESH["tools"][0])

    def _run(self, *extra, **kwargs):
        return self.run_main(
            tools=self.TOOLS, users=("alice",), fresh_report=self.FRESH,
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                # Left untried by the drain, so it is still owed when detection runs.
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
            ) + extra,
            **kwargs,
        )

    def test_the_correcting_upload_goes_out_despite_a_matching_hash(self):
        backend, _ = self._run(
            patch.object(adm.discovery_cache, "get_cached_hash",
                         return_value=self.matching),
        )

        self.assertEqual(backend.device_ids(prefix="QUEUED-"), [],
                         "the drain delivered it, so the queue is not what made"
                         " detection distrust the hash")
        self.assertIn(
            "THIS-RUN", backend.device_ids(prefix="THIS-RUN"),
            "a matching hash suppressed the upload while an older report for the"
            " same pair sat in the queue, so a row the drain may already have"
            " replaced stays wrong until the tool's config changes",
        )

    def test_the_upload_is_what_drops_the_queued_copy(self):
        backend, _ = self._run()

        self.assertIn("THIS-RUN", backend.device_ids(prefix="THIS-RUN"))
        self.assertEqual(
            self.queue_now(), [],
            "this run replaced that row with a newer snapshot, so sending the"
            " queued one later would undo the upload",
        )

    def test_an_upload_that_fails_leaves_the_queued_copy_alone(self):
        """Nothing was replaced, so nothing is superseded: both are still owed."""
        backend, _ = self._run(rule=status_for(permanent=()), domain="http://127.0.0.1:1")

        self.assertEqual(
            sorted(self.queue_now()), ["QUEUED-0", "THIS-RUN"],
            "the run could not reach the backend, so dropping either of them"
            " loses a report nothing will send again",
        )


class ABodyTooLargeForOneRouteIsNotALostReport(_DrivesMain, unittest.TestCase):
    """Every report with tools in it goes through S3 first, and S3 is there for the
    bodies the legacy endpoint refuses. So when S3 fails for its own reasons and
    the fallback answers 413, the report is not bad -- the route is. Discarding it
    loses that device's data until the tool's config changes."""

    BIG = {"device_id": "QUEUED-0", "home_user": "alice",
           "tools": [{"name": "Claude Code", "projects": []}]}

    def test_a_fallback_refusal_keeps_the_report_for_the_other_route(self):
        self.seed_queue([dict(self.BIG)])

        # The stub refuses the presign, which is what sends a real report down the
        # legacy fallback in the first place.
        backend, _ = self.run_main(rule=statuses({"QUEUED-0": 413}))

        self.assertEqual(backend.attempts("QUEUED-0"), 1,
                         "asking the same endpoint again cannot change the answer")
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "a report only the S3 route can carry was dropped at the legacy"
            " endpoint, so nothing will ever try the route that works",
        )

    def test_two_oversized_reports_do_not_stop_the_drain(self):
        """A body the legacy endpoint will not take says nothing about whether the
        backend is healthy. Counted as failures, two of them at the front of the
        queue strand everything behind them."""
        big = [dict(self.BIG, device_id=f"QUEUED-{i}") for i in range(3)]
        self.seed_queue(big)

        backend, _ = self.run_main(
            rule=statuses({"QUEUED-0": 413, "QUEUED-1": 413}),
        )

        self.assertEqual(
            backend.device_ids(prefix="QUEUED-"),
            ["QUEUED-0", "QUEUED-1", "QUEUED-2"],
            "the drain stopped on two reports the legacy endpoint merely could"
            " not take, so the healthy one behind them was never tried",
        )
        self.assertEqual(sorted(self.queue_now()), ["QUEUED-0", "QUEUED-1"],
                         "the two it could not place stay for the S3 route;"
                         " the delivered one is gone")

    def test_a_report_the_backend_will_never_accept_is_still_discarded(self):
        """The counterweight: 422 is about the report, so keeping it would mean
        re-sending it every run for a day."""
        self.seed_queue([dict(self.BIG)])

        self.run_main(rule=statuses({"QUEUED-0": 422}))

        self.assertEqual(self.queue_now(), [])


class AnUnreachableBackendIsConclusive(_DrivesMain, unittest.TestCase):
    """A refused connection is not the same as a refused report. Nothing reached
    the backend, so nothing behind this one will either -- there is no payload to
    give the benefit of the doubt to, and no reason to sleep out a retry curve
    that cannot connect."""

    DEAD = "http://127.0.0.1:1"

    def _posted_device_ids(self):
        """The device id of every curl POST the sender makes. The lifecycle events
        share the report endpoint, so counting URLs would conflate them with the
        drain; the payload curl is handed names the report itself."""
        posted = []
        real_run = utils_mod.subprocess.run

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd and cmd[0] == "curl" and "-d" in cmd:
                body = cmd[cmd.index("-d") + 1]
                if isinstance(body, str) and body.startswith("@"):
                    try:
                        posted.append(json.loads(
                            Path(body[1:]).read_text()).get("device_id"))
                    except Exception:
                        pass
            return real_run(cmd, *a, **kw)

        return posted, patch.object(utils_mod.subprocess, "run", spy)

    def test_one_refused_connection_ends_the_drain(self):
        pending = _queued(10)
        self.seed_queue(pending)
        posted, spy = self._posted_device_ids()

        with spy:
            self.run_main(domain=self.DEAD)

        drained = [d for d in posted if (d or "").startswith("QUEUED-")]
        # The request count is the proof, not elapsed time: a refused connection
        # returns instantly, so a regression that retried all ten would still be
        # quick. Ten queued reports used to cost fifty requests here.
        self.assertEqual(
            drained, ["QUEUED-0"],
            "a backend that refused the connection was asked again; one answer of"
            " 'nothing listening' settles it for the whole queue",
        )
        self.assertEqual(
            self.queue_now(), [r["device_id"] for r in pending],
            "all ten are still owed, so all ten stay queued",
        )


if __name__ == "__main__":
    unittest.main()
