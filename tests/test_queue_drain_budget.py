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


class _DrivesMain:
    """Runs main() with detection and transport stubbed, and reports what the
    drain actually did: which reports it sent, in what order relative to
    detection, and what it handed to the queue."""

    def run_main(self, pending, outcome, timeout="12000", patches=(),
                 real_queue=False, tools=(), users=(), fresh_report=None):
        sent = []
        order = []

        def _send(domain, api_key, report, app_name=None, sentry_context=None, **_kwargs):
            sent.append(report)
            order.append("send")
            return outcome(report) if callable(outcome) else outcome

        def _detect(*_args, **_kwargs):
            order.append("detect")
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

        saved = Mock()
        argv = [
            "ai_tools_discovery.py", "--api-key", "k",
            "--domain", "http://127.0.0.1:1", "--timeout", timeout,
        ]
        with ExitStack() as stack:
            for ctx in (
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
                patch.object(adm, "send_scan_event", Mock(return_value=(True, None))),
                patch.object(adm, "send_discovery_metrics", Mock()),
                patch.object(adm, "send_report_to_backend", _send),
            ) + ((
                patch.object(adm, "load_pending_reports", return_value=list(pending)),
                patch.object(adm, "save_failed_reports", saved),
            ) if not real_queue else ()) + (
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
        return sent, saved, order

    def requeued(self, saved):
        """What main() last left on the queue, and how. The queue is rewritten
        whenever what is owed changes, so the final call is the one that counts."""
        self.assertGreaterEqual(saved.call_count, 1, "the queue was never written")
        args, kwargs = saved.call_args
        self.assertTrue(kwargs.get("replace"), "the queue write was not authoritative")
        return args[0]

    def ids(self, reports):
        return [r["device_id"] for r in reports]


class TheDrainCannotStarveDetection(_DrivesMain, unittest.TestCase):
    """The drain runs ahead of this run's own sends, so what has to hold is that
    it can never stop detection from happening."""

    def test_a_full_queue_still_gets_drained_and_detection_still_runs(self):
        sent, _saved, order = self.run_main(_queued(3), SENT_OK)

        self.assertEqual(self.ids(sent), ["QUEUED-0", "QUEUED-1", "QUEUED-2"])
        self.assertIn("detect", order, "detection never ran")

    def test_an_undrainable_queue_does_not_cost_the_run(self):
        """What timed out in production: a full queue, none of it sendable."""
        sent, _saved, order = self.run_main(_queued(100), FAILED_RETRYABLE)

        self.assertEqual(
            len(sent), adm.DRAIN_FAILURES_BEFORE_STOP,
            "the drain paid a retry cycle per queued report before detecting anything",
        )
        self.assertIn("detect", order, "the run never reached detection")


class AnUnreachableBackendStopsTheDrain(_DrivesMain, unittest.TestCase):
    """Retryable failures in a row are the transport, not the payloads: every
    report behind them would pay its own retry cycle to fail the same way."""

    def test_a_run_of_failures_leaves_the_rest_untried(self):
        pending = _queued(5)
        sent, saved, _order = self.run_main(pending, FAILED_RETRYABLE)

        self.assertEqual(
            self.ids(sent), ["QUEUED-0", "QUEUED-1"],
            "the drain kept going after the backend proved unreachable",
        )
        self.assertEqual(self.ids(self.requeued(saved)), self.ids(pending))

    def test_a_failure_part_way_through_requeues_only_what_is_left(self):
        pending = _queued(5)
        reachable = {"QUEUED-0", "QUEUED-1"}
        sent, saved, _order = self.run_main(
            pending,
            lambda r: SENT_OK if r["device_id"] in reachable else FAILED_RETRYABLE,
        )

        self.assertEqual(self.ids(sent), ["QUEUED-0", "QUEUED-1", "QUEUED-2", "QUEUED-3"])
        self.assertEqual(
            self.ids(self.requeued(saved)), ["QUEUED-2", "QUEUED-3", "QUEUED-4"],
            "delivered reports were re-queued, or the untried tail was dropped",
        )

    def test_one_report_that_keeps_failing_does_not_block_the_queue(self):
        """A lone retryable failure is about that payload. Stopping on it would
        strand every healthy report behind it until the queue expired."""
        pending = _queued(5)
        sent, saved, _order = self.run_main(
            pending,
            lambda r: FAILED_RETRYABLE if r["device_id"] == "QUEUED-2" else SENT_OK,
        )

        self.assertEqual(self.ids(sent), self.ids(pending), "the drain stopped early")
        self.assertEqual(self.ids(self.requeued(saved)), ["QUEUED-2"])

    def test_a_permanent_failure_does_not_stop_the_drain(self):
        """The counterweight: a 4xx is about one payload, so the queue behind it
        is still worth draining."""
        pending = _queued(3)
        sent, saved, _order = self.run_main(pending, FAILED_PERMANENT)

        self.assertEqual(self.ids(sent), self.ids(pending))
        self.assertEqual(self.requeued(saved), [], "a rejected report was re-queued")


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
        sent, saved, order = self.run_main(
            pending, SENT_OK,
            patches=(patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),),
        )

        self.assertEqual(sent, [], "the drain ran with no budget left")
        self.assertIn("detect", order, "detection was skipped too")
        self.assertEqual(self.ids(self.requeued(saved)), self.ids(pending))


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


class TheQueueFileEndsAsWhatIsStillOwed(_DrivesMain, unittest.TestCase):
    """End to end through main() and the real queue file: a run leaves behind
    exactly the reports it still owes -- no delivered ones, no duplicates."""

    def setUp(self):
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-e2e-test-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_delivered_reports_are_gone_and_the_untried_tail_remains(self):
        pending = _queued(5)
        now = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": now} for r in pending]
        ))
        reachable = {"QUEUED-0", "QUEUED-1"}

        sent, _saved, _order = self.run_main(
            None,
            lambda r: SENT_OK if r["device_id"] in reachable else FAILED_RETRYABLE,
            real_queue=True,
        )

        left = [e["report"]["device_id"] for e in json.loads(self.queue_file.read_text())]
        self.assertEqual(
            left, ["QUEUED-2", "QUEUED-3", "QUEUED-4"],
            "the queue kept reports it had already delivered, so the next run"
            " spends its budget re-sending them",
        )
        self.assertEqual(self.ids(sent), ["QUEUED-0", "QUEUED-1", "QUEUED-2", "QUEUED-3"])

    def test_what_is_written_back_has_been_through_the_supersede_filter(self):
        """The wiring between the two: reaching the per-tool send path in-process
        means mocking the whole extraction pipeline, so the filter's own rule is
        unit-tested and this asserts main() actually routes the backlog through it."""
        seen = {}

        def _spy(queued, reported_this_run):
            seen["queued"] = list(queued)
            seen["reported"] = set(reported_this_run)
            return list(queued)

        pending = _queued(3)
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": datetime.now(timezone.utc).isoformat()}
             for r in pending]
        ))

        self.run_main(None, FAILED_RETRYABLE, real_queue=True,
                      patches=(patch.object(adm, "_drop_superseded", _spy),))

        self.assertEqual(self.ids(seen["queued"]), self.ids(pending))
        self.assertIsInstance(seen["reported"], set)

    def test_a_fully_drained_queue_removes_the_file(self):
        pending = _queued(3)
        now = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": now} for r in pending]
        ))

        sent, _saved, _order = self.run_main(None, SENT_OK, real_queue=True)

        self.assertEqual(self.ids(sent), self.ids(pending))
        self.assertFalse(self.queue_file.exists())


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
        with patch("time.sleep") as slept:
            success, retryable = send_report_to_backend(
                self.url, "test-key-000000", self.report,
                deadline=time.monotonic() + 3,
            )

        self.assertEqual((success, retryable), (False, True))
        self.assertEqual(self.server.hits, 1, "it retried past its deadline")
        self.assertEqual(slept.call_count, 0, "it slept a backoff it had no budget for")

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

    def setUp(self):
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-supersede-test-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def _seed(self, reports):
        now = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": now} for r in reports]
        ))

    def test_the_drop_happens_at_the_upload_not_at_the_final_write(self):
        """The invariant: by the time the stored hash can make a later run skip
        this pair, the older queued report is already out of the file."""
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        self._seed([superseded])
        writes = []
        real_save = utils_mod.save_failed_reports

        def _spy(reports, **kwargs):
            writes.append([r.get("device_id") for r in reports])
            return real_save(reports, **kwargs)

        def _outcome(report):
            if report.get("device_id") != "THIS-RUN":
                return FAILED_RETRYABLE
            writes.append("UPLOADED")
            return SENT_OK

        fresh = {"home_user": "alice", "device_id": "THIS-RUN", "system_user": None,
                 "tools": [{"name": "Claude Code", "projects": []}]}
        self.run_main(
            None,
            _outcome,
            real_queue=True,
            tools=({"name": "Claude Code", "version": "1.0",
                    "install_path": "/usr/local/bin/claude"},),
            users=("alice",),
            fresh_report=fresh,
            patches=(
                patch.object(adm, "save_failed_reports", _spy),
                patch.object(adm, "DRAIN_FAILURES_BEFORE_STOP", 99),
                patch.object(adm, "DRAIN_BUDGET_SECONDS", 0),
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=Path(self._dir)),
                patch.object(adm.discovery_cache, "get_cached_hash", return_value=None),
                patch.object(adm.discovery_cache, "record_report", Mock()),
            ),
        )

        self.assertIn("UPLOADED", writes, "the fresh report never went out")
        after_upload = writes[writes.index("UPLOADED") + 1:]
        self.assertTrue(after_upload, "the queue was not written after the upload")
        self.assertNotIn(
            "Q-OLD", after_upload[0],
            "the superseded report survived the first write after the upload, so a"
            " kill before persist would leave it to overwrite this snapshot",
        )

    def test_a_delivered_report_leaves_the_file_before_detection_begins(self):
        """A report the drain has settled must reach disk straight away. An abort
        later in the run skips the persist step entirely (the watchdog calls
        os._exit), and the next run would send that delivered report again -- after
        a stored hash has already stopped detection from correcting it."""
        delivered = {"device_id": "Q-GONE", "home_user": "alice",
                     "tools": [{"name": "Claude Code"}]}
        self._seed([delivered])
        at_detection = {}

        def _probe():
            at_detection["queue"] = (
                [e["report"]["device_id"]
                 for e in json.loads(self.queue_file.read_text())]
                if self.queue_file.exists() else []
            )
            return []

        sent, _saved, _order = self.run_main(
            None, SENT_OK, real_queue=True,
            patches=(patch.object(adm, "get_all_users_linux", side_effect=_probe),),
        )

        self.assertEqual(self.ids(sent), ["Q-GONE"], "the drain never sent it")
        self.assertEqual(
            at_detection.get("queue"), [],
            "a report the drain had already delivered was still on disk when"
            " detection began, so an abort would send it a second time",
        )

    def test_a_pair_the_resumed_run_already_reported_is_dropped_unsent(self):

        """Known before the drain starts, so the drain can refuse it outright
        rather than send it and rely on the requeue filter afterwards."""
        superseded = {"device_id": "Q-OLD", "home_user": "alice",
                      "tools": [{"name": "Claude Code"}]}
        other = {"device_id": "Q-KEEP", "home_user": "bob",
                 "tools": [{"name": "Cursor"}]}
        self._seed([superseded, other])

        sent, _saved, _order = self.run_main(
            None, SENT_OK, real_queue=True,
            patches=(
                patch.object(adm.discovery_cache, "resumable_done",
                             return_value={("Claude Code:/usr/local/bin/claude", "alice")}),
            ),
        )

        self.assertEqual(
            self.ids(sent), ["Q-KEEP"],
            "a report the resumed run had already superseded was sent anyway",
        )
        self.assertFalse(self.queue_file.exists(), "the dropped report was re-queued")


if __name__ == "__main__":
    unittest.main()
