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
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    transport underneath it."""

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
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
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


def statuses(table, default=200):
    """A rule mapping device id to HTTP status: 500 is retryable, 422 is not."""
    return lambda body: table.get(body.get("device_id"), default)


def lost_then_refused():
    """The first answer is lost and the second is a permanent refusal -- the shape
    of an upload that may have landed and will never be accepted again."""
    seen = {"n": 0}

    def _rule(body):
        if not (body.get("device_id") or "").startswith("QUEUED-"):
            return 200
        seen["n"] += 1
        return None if seen["n"] == 1 else 413
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
                lambda *_a, **_k: dict(fresh_report)
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
        """What timed out in production: a full queue, none of it sendable."""
        pending = _queued(100)
        self.seed_queue(pending)

        backend, detected = self.run_main(rule=status_for())

        self.assertEqual(
            len(backend.device_ids()), adm.DRAIN_FAILURES_BEFORE_STOP,
            "the drain paid a retry cycle per queued report before detecting anything",
        )
        self.assertTrue(detected, "the run never reached detection")
        self.assertEqual(len(self.queue_now()), len(pending))


class AnUnreachableBackendStopsTheDrain(_DrivesMain, unittest.TestCase):
    """Retryable failures in a row are the transport, not the payloads: every
    report behind them would pay its own retry cycle to fail the same way."""

    def test_a_run_of_failures_leaves_the_rest_untried(self):
        pending = _queued(5)
        self.seed_queue(pending)

        backend, _ = self.run_main(rule=status_for())

        self.assertEqual(
            backend.device_ids(), ["QUEUED-0", "QUEUED-1"],
            "the drain kept going after the backend proved unreachable",
        )
        self.assertEqual(self.queue_now(), [r["device_id"] for r in pending])

    def test_each_failure_really_paid_its_retry_cycle(self):
        """Through the real transport: one unreachable report is five curl calls,
        which is the cost the budget and the early stop exist to bound."""
        self.seed_queue(_queued(5))

        backend, _ = self.run_main(rule=status_for())

        self.assertEqual(backend.attempts("QUEUED-0"), utils_mod.MAX_ATTEMPTS)
        self.assertEqual(backend.attempts("QUEUED-2"), 0, "a report past the stop was tried")

    def test_a_failure_part_way_through_requeues_only_what_is_left(self):
        self.seed_queue(_queued(5))

        backend, _ = self.run_main(rule=status_for(ok={"QUEUED-0", "QUEUED-1"}))

        self.assertEqual(backend.device_ids(), ["QUEUED-0", "QUEUED-1", "QUEUED-2", "QUEUED-3"])
        self.assertEqual(
            self.queue_now(), ["QUEUED-2", "QUEUED-3", "QUEUED-4"],
            "delivered reports were re-queued, or the untried tail was dropped",
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
        self.assertEqual(s3_mod._within(30, None), 30)
        self.assertEqual(s3_mod._within(30, time.monotonic() + 100), 30)
        self.assertIn(s3_mod._within(180, time.monotonic() + 10), (9, 10))

    def test_a_passed_deadline_starts_no_step(self):
        self.assertIsNone(s3_mod._within(30, time.monotonic() - 1))
        self.assertEqual(
            s3_mod.try_s3_upload(
                "http://127.0.0.1:1", "k", {"device_id": "D", "tools": [{"name": "T"}]},
                deadline=time.monotonic() - 1,
            ),
            (False, True),
        )


class ASupersededReportIsNeverSent(_DrivesMain, unittest.TestCase):
    """Dropping a superseded report only at the final queue write is too late: the
    payload hash is stored the moment an upload succeeds, so a kill in between
    leaves the older report to replace that snapshot on the next run -- and it
    stays, because the stored hash makes detection skip the correcting upload."""

    def test_the_queue_is_settled_before_the_hash_that_hides_the_pair(self):
        """The invariant, observed where it matters: at the instant the payload hash
        is stored, the older queued report is already out of the file."""
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        self.seed_queue([superseded])
        # The stored hash is the thing that makes a later run skip the correcting
        # upload, so the queue file is observed at the moment it is written.
        at_hash_write = {}

        def _record_report(*_a, **_k):
            at_hash_write["queue"] = (
                [e["report"]["device_id"]
                 for e in json.loads(self.queue_file.read_text())]
                if self.queue_file.exists() else []
            )

        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.run_main(
            rule=statuses({"Q-OLD": 500}),
            tools=({"name": "Claude Code", "version": "1.0",
                    "install_path": "/usr/local/bin/claude"},),
            users=("alice",),
            fresh_report=fresh,
            patches=(
                patch.object(adm, "DRAIN_FAILURES_BEFORE_STOP", 99),
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report",
                             side_effect=_record_report),
            ),
        )

        self.assertIn("queue", at_hash_write, "no hash was ever stored")
        self.assertNotIn(
            "Q-OLD", at_hash_write["queue"],
            "the superseded report was still on disk when the hash was stored, so a"
            " kill in between would restore stale data for good",
        )

    def test_a_delivered_report_leaves_the_file_before_detection_begins(self):
        """A report the drain has settled must reach disk straight away. An abort
        later in the run skips the persist step entirely (the watchdog calls
        os._exit), and the next run would send that delivered report again -- after
        a stored hash has already stopped detection from correcting it."""
        delivered = {"device_id": "Q-GONE", "home_user": "alice",
                     "tools": [{"name": "Claude Code"}]}
        self.seed_queue([delivered])
        at_detection = {}

        def _probe():
            at_detection["queue"] = (
                [e["report"]["device_id"]
                 for e in json.loads(self.queue_file.read_text())]
                if self.queue_file.exists() else []
            )
            return []

        backend, _ = self.run_main(
            patches=(patch.object(adm, "get_all_users_linux", side_effect=_probe),),
        )

        self.assertEqual(
            backend.device_ids(prefix="Q-"), ["Q-GONE"], "the drain never sent it"
        )
        self.assertEqual(
            at_detection.get("queue"), [],
            "a report the drain had already delivered was still on disk when"
            " detection began, so an abort would send it a second time",
        )

    def test_a_failed_queue_write_stops_the_hash_being_stored(self):
        """The ordering only helps if the write actually landed. Storing the hash
        after a failed rewrite would hide the pair while the stale report is still
        queued -- the exact outcome the ordering exists to prevent."""
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        self.seed_queue([superseded])
        recorded = []
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}

        self.run_main(
            rule=statuses({"Q-OLD": 500}),
            tools=({"name": "Claude Code", "version": "1.0",
                    "install_path": "/usr/local/bin/claude"},),
            users=("alice",),
            fresh_report=fresh,
            patches=(
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
                patch.object(adm, "save_failed_reports", return_value=False),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report",
                             side_effect=lambda *a, **k: recorded.append(a)),
            ),
        )

        self.assertEqual(
            recorded, [],
            "the hash was stored although the queue write failed, so the stale"
            " report is both queued and hidden",
        )

    def test_a_failed_post_drain_write_also_stops_the_hash(self):
        """The drain delivering a report leaves nothing to drop later, so the
        per-upload rewrite would not fire and the post-drain write is the only one
        that cleaned the file. Its failure has to count too, or a delivered report
        stays on disk while hashes hide the pairs that would correct it."""
        delivered = {"device_id": "Q-DELIVERED", "home_user": "bob",
                     "tools": [{"name": "Cursor"}]}
        self.seed_queue([delivered])
        recorded = []
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}

        backend, _ = self.run_main(
            tools=({"name": "Claude Code", "version": "1.0",
                    "install_path": "/usr/local/bin/claude"},),
            users=("alice",),
            fresh_report=fresh,
            patches=(
                patch.object(adm, "save_failed_reports", return_value=False),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report",
                             side_effect=lambda *a, **k: recorded.append(a)),
            ),
        )

        self.assertIn(
            "Q-DELIVERED", backend.device_ids(prefix="Q-"),
            "the drain never delivered it",
        )
        self.assertEqual(
            recorded, [],
            "a hash was stored although the write that should have cleared the"
            " delivered report from disk had failed",
        )

    def test_a_pair_the_backlog_does_not_hold_costs_no_extra_write(self):
        """Once the post-drain write has landed, the file holds only what is owed,
        so a pair that is not in it needs no further write. Rewriting per upload
        re-reads and re-serialises every queued report, which on a device with a
        backlog and several tools is pure cost."""
        unrelated = {"device_id": "Q-OTHER", "home_user": "bob",
                     "tools": [{"name": "Cursor"}]}
        self.seed_queue([unrelated])
        writes = []
        real_save = utils_mod.save_failed_reports
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}

        self.run_main(
            rule=statuses({"Q-OLD": 500}),
            tools=({"name": "Claude Code", "version": "1.0",
                    "install_path": "/usr/local/bin/claude"},),
            users=("alice",),
            fresh_report=fresh,
            patches=(
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
                patch.object(adm, "save_failed_reports",
                             side_effect=lambda *a, **k: (
                                 writes.append(1), real_save(*a, **k))[1]),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report", Mock()),
            ),
        )

        # Two: once when the drain finishes, once at persist. A third would be a
        # rewrite for a pair the file never held.
        self.assertEqual(
            len(writes), 2,
            "the queue was rewritten for a pair it never held",
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

    def test_a_pair_the_resumed_run_already_reported_is_dropped_unsent(self):

        """Known before the drain starts, so the drain can refuse it outright
        rather than send it and rely on the requeue filter afterwards."""
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        other = {"device_id": "Q-KEEP", "home_user": "bob",
                 "tools": [{"name": "Cursor"}]}
        self.seed_queue([superseded, other])

        backend, _ = self.run_main(
            patches=(
                patch.object(adm.discovery_cache, "resumable_done",
                             return_value={("Claude Code:/usr/local/bin/claude", "alice")}),
            ),
        )

        self.assertEqual(
            backend.device_ids(prefix="Q-"), ["Q-KEEP"],
            "a report the resumed run had already superseded was sent anyway",
        )
        self.assertFalse(self.queue_file.exists(), "the dropped report was re-queued")


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

    def test_the_completed_event_finishes_before_the_watchdog(self):
        """It carries the manifest the backend prunes against, and a watchdog that
        fires over it reports the run failed even though it did its work."""
        seen = {}

        def _event(domain, api_key, device_id, run_id, event, *a, **kw):
            if event == "completed":
                seen["deadline"] = kw.get("deadline")
            return (True, None)

        started = time.monotonic()
        self.run_main(
            timeout="900",
            patches=(patch.object(adm, "send_scan_event", _event),),
        )

        self.assertIn("deadline", seen, "no completed event was sent")
        # Behavioural rather than a reference to the constant: the deadline has to
        # be meaningfully earlier than the watchdog, not the same instant.
        watchdog_at = started + 900
        self.assertLess(
            seen["deadline"], watchdog_at - 5,
            "the completed event is given until the watchdog fires, so it can"
            " still be in flight when the run is killed over it",
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


class DeliveringAQueuedReportInvalidatesItsHash(_DrivesMain, unittest.TestCase):
    """A queued report exists because its upload failed, so the cache still holds
    the hash of an *earlier* upload. Delivering it replaces the backend snapshot
    with that older payload, and the stale hash would then make detection skip
    the upload that would correct it -- for good, because the hash keeps matching
    until something changes on the device."""

    def test_the_pair_it_delivered_is_no_longer_treated_as_current(self):
        forgotten = []
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        self.run_main(
            patches=(patch.object(
                adm.discovery_cache, "forget_tool_hash",
                side_effect=lambda t, u: (forgotten.append((u, t)), True)[1],
            ),),
        )

        self.assertIn(
            ("alice", "Claude Code"), forgotten,
            "the drain left a hash in place that no longer describes the backend,"
            " so detection will skip the upload that would correct it",
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

    def test_a_failed_cache_write_does_not_let_the_stale_hash_win(self):
        """When the write is reported as failed and the stale hash survives on
        disk, the correcting upload still has to go out in THIS run, carried by
        the in-memory set. What happens to the queue entry afterwards is the
        sibling test's subject: here the run does correct the pair, so dropping
        it is right."""
        tools = ({"name": "Claude Code", "version": "1.0",
                  "install_path": "/usr/local/bin/claude"},)
        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])
        # The cached hash has to be the one production will actually compute for
        # this payload, or the dedup branch is never reached and the test passes
        # whether the guard exists or not.
        stale_hash = compute_payload_hash(fresh["tools"][0])

        backend, _ = self.run_main(
            tools=tools, users=("alice",), fresh_report=fresh,
            patches=(
                # The invalidation reports that it never reached disk, and the old
                # hash still matches. A bare Mock would not do: it is truthy, so
                # main() would read it as a successful write.
                patch.object(adm.discovery_cache, "forget_tool_hash",
                             return_value=False),
                patch.object(adm.discovery_cache, "get_cached_hash",
                             return_value=stale_hash),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "record_report", Mock()),
            ),
        )

        self.assertIn("QUEUED-0", backend.device_ids(), "the drain never delivered it")
        self.assertIn(
            "THIS-RUN", backend.device_ids(prefix="THIS-RUN"),
            "a stale hash on disk suppressed the correcting upload, so the row the"
            " drain replaced stays wrong for good",
        )

    def test_an_invalidation_that_did_not_land_keeps_the_report_queued(self):
        """The in-memory set only lasts this process. If the hash survived the
        write, the report has to stay queued so a later run retries -- otherwise
        an interruption here leaves a row that the stale hash hides for good."""
        stale = {"device_id": "QUEUED-0", "home_user": "alice",
                 "tools": [{"name": "Claude Code"}]}
        self.seed_queue([stale])

        backend, _ = self.run_main(
            patches=(
                # Delivered, but the cache says the hash never left disk.
                patch.object(adm.discovery_cache, "forget_tool_hash",
                             return_value=False),
            ),
        )

        self.assertEqual(backend.device_ids(), ["QUEUED-0"], "it was never delivered")
        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "a delivered report was dropped although its invalidation never"
            " landed, so nothing will retry it",
        )

    def test_a_refusal_after_a_lost_answer_still_clears_the_hash(self):
        """413 is final, so the report is rightly discarded -- but an earlier
        attempt may already have put it on the backend, and the stored hash would
        then describe a row that is no longer there. Nothing would be left to
        correct it: the report is gone."""
        forgotten = []
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        backend, _ = self.run_main(
            rule=lost_then_refused(),
            patches=(patch.object(
                adm.discovery_cache, "forget_tool_hash",
                side_effect=lambda t, u: (forgotten.append((u, t)), True)[1],
            ),),
        )

        self.assertEqual(backend.attempts("QUEUED-0"), 2,
                         "the lost answer has to be retried for this to be the"
                         " uncertain-then-refused path at all")
        self.assertEqual(
            forgotten, [("alice", "Claude Code")],
            "a report that may have reached the backend was discarded with its"
            " hash intact, so detection trusts a row that may be stale",
        )
        self.assertEqual(self.queue_now(), [],
                         "413 is permanent; requeueing it would never end")

    def test_a_refusal_whose_invalidation_failed_keeps_the_report(self):
        """The set that remembers the pair dies with the process, so when the write
        does not land the report has to stay -- its presence is what makes the next
        run distrust the hash, whatever that run manages to send."""
        self.seed_queue([{"device_id": "QUEUED-0", "home_user": "alice",
                          "tools": [{"name": "Claude Code"}]}])

        self.run_main(
            rule=lost_then_refused(),
            patches=(patch.object(adm.discovery_cache, "forget_tool_hash",
                                  return_value=False),),
        )

        self.assertEqual(
            self.queue_now(), ["QUEUED-0"],
            "the hash stayed on disk and the report that would have exposed it was"
            " discarded, so an interrupted run leaves the row hidden for good",
        )

    def test_a_report_it_could_not_deliver_leaves_the_hash_alone(self):
        """Only a delivery makes the hash wrong. Clearing it on a failure would
        force a pointless re-upload of a pair the backend never heard about."""
        forgotten = []
        self.seed_queue(_queued(1))

        self.run_main(
            rule=status_for(),
            patches=(patch.object(
                adm.discovery_cache, "forget_tool_hash",
                side_effect=lambda t, u: (forgotten.append((u, t)), True)[1],
            ),),
        )

        self.assertEqual(forgotten, [])

    def test_forgetting_leaves_other_pairs_and_the_rest_of_the_cache(self):
        cache = {"tools": {"Claude Code": {"alice": {"payload_hash": "h1"},
                                           "bob": {"payload_hash": "h2"}},
                           "Cursor": {"alice": {"payload_hash": "h3"}}},
                 "run": {"run_id": "keep-me"}}
        written = {}
        with patch.object(adm.discovery_cache, "read_cache", return_value=cache), \
             patch.object(adm.discovery_cache, "atomic_write_cache",
                          side_effect=lambda c: written.update(c)):
            adm.discovery_cache.forget_tool_hash("Claude Code", "alice")

        self.assertNotIn("alice", written["tools"]["Claude Code"])
        self.assertIn("bob", written["tools"]["Claude Code"])
        self.assertEqual(written["tools"]["Cursor"]["alice"]["payload_hash"], "h3")
        self.assertEqual(written["run"]["run_id"], "keep-me")


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
