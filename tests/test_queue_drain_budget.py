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
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.ai_tools_discovery as adm
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    MAX_QUEUE_SIZE,
    load_pending_reports,
    save_failed_reports,
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
                 real_queue=False):
        sent = []
        order = []

        def _send(domain, api_key, report, app_name=None, sentry_context=None):
            sent.append(report)
            order.append("send")
            return outcome(report) if callable(outcome) else outcome

        def _detect(*_args, **_kwargs):
            order.append("detect")
            return []

        detector = Mock()
        detector.get_device_id.return_value = "test-device"
        detector.detect_all_tools.side_effect = _detect
        detector.skills_metrics = {}

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
                patch.object(adm, "get_all_users_linux", return_value=[]),
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
        """The reports main() handed back to the queue, and how."""
        self.assertEqual(saved.call_count, 1, "the queue was not written exactly once")
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
        self.assertEqual(saved.call_count, 0, "a rejected report was re-queued")


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

    def test_a_fully_drained_queue_removes_the_file(self):
        pending = _queued(3)
        now = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": r, "queued_at": now} for r in pending]
        ))

        sent, _saved, _order = self.run_main(None, SENT_OK, real_queue=True)

        self.assertEqual(self.ids(sent), self.ids(pending))
        self.assertFalse(self.queue_file.exists())


if __name__ == "__main__":
    unittest.main()
