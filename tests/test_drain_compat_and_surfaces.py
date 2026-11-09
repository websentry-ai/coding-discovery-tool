"""Old callers against the new code, and the formats that cross versions.

The queue drain change added an optional deadline to four send functions and a
replace flag to the queue writer. Every existing caller passes neither, and the
queue and cache files on a device outlive any single release -- a rollback has
to be able to read what the new code wrote. These are the cases a diff does not
show, because nothing was renamed.
"""

import json
import os
import shutil
import tempfile
import subprocess
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.cache as cache_mod
import scripts.coding_discovery_tools.s3_uploader as s3_mod
import scripts.coding_discovery_tools.sweep_connectors as sweep_mod
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    load_pending_reports,
    save_failed_reports,
    send_report_to_backend,
    send_scan_event,
)

# Every key the report POST may carry. Frozen deliberately: the deadline work is
# client-side only, so a new key here means the wire contract moved and the
# backend has to be considered.
REPORT_KEYS = {
    "device_id", "home_user", "system_user", "tools", "run_id", "app_name",
    "tool_name", "payload_hash", "scan_event", "scan_error", "manifest",
    "covered_home_users", "is_container", "sentry_metrics", "timestamp",
    "device_discovery_id",
}


class _Recorder(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        self.server.bodies.append(raw)
        if getattr(self.server, "stall", 0):
            self.server.stalling.wait(self.server.stall)
        code = self.server.code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}' if code < 300 else b'{"error":"no"}')

    def log_message(self, *_a):
        pass


class _ServesABackend(unittest.TestCase):
    """A localhost backend plus an isolated queue file."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.server = HTTPServer(("127.0.0.1", 0), _Recorder)
        self.server.bodies = []
        self.server.code = 200
        self.server.stall = 0
        self.server.stalling = threading.Event()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-compat-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
        self.server.stalling.set()
        self.server.shutdown()
        self.server.server_close()
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def sent_bodies(self):
        return [json.loads(b) for b in self.server.bodies]


class OldCallersStillWork(_ServesABackend):
    """Each send function gained an optional deadline, and every caller that
    exists today passes none. Calling them the old way must behave exactly as it
    did, including keeping the whole retry curve."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}

    def test_a_five_positional_report_send_still_works(self):
        """`sentry_context` was the fifth positional argument; `deadline` is the
        sixth. A caller passing five positionally must not land on it."""
        ok, retryable = send_report_to_backend(
            self.url, "test-key-000000", dict(self.REPORT), None, {"phase": "t"},
        )

        self.assertEqual((ok, retryable), (True, False))
        self.assertEqual(len(self.server.bodies), 1)

    def test_a_report_send_without_a_deadline_keeps_every_attempt(self):
        self.server.code = 500

        with patch("time.sleep", Mock()):
            ok, retryable = send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT),
            )

        self.assertEqual((ok, retryable), (False, True))
        self.assertEqual(len(self.server.bodies), utils_mod.MAX_ATTEMPTS)

    def test_a_scan_event_without_a_deadline_still_sends(self):
        ok, _ = send_scan_event(
            self.url, "test-key-000000", "dev", "run-1", "in_progress",
        )

        self.assertTrue(ok)
        self.assertEqual(self.sent_bodies()[0]["scan_event"], "in_progress")

    def _max_times(self, call):
        """The --max-time values curl was actually given, in order."""
        return [given for given, _cap in self._budgets(call)]

    def _budgets(self, call):
        """Per request: the --max-time curl was given, and the process-level cap
        behind it."""
        seen = []
        real_run = s3_mod.subprocess.run

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd:
                seen.append((int(cmd[cmd.index("--max-time") + 1]), kw.get("timeout")))
            return real_run(cmd, *a, **kw)

        with patch.object(s3_mod.subprocess, "run", spy):
            call()
        return seen

    def test_an_s3_request_cannot_outlive_its_deadline_by_the_tail(self):
        """The legacy send keeps its process cap within a second of a deadline,
        because an end-of-run tail can be under five seconds on a short budget.
        The S3 steps are clipped by the same deadline and need the same bound."""
        self.server.code = 500

        budgets = self._budgets(lambda: s3_mod.try_s3_upload(
            self.url, "test-key-000000",
            {"device_id": "D", "tools": [{"name": "T"}]}, {"phase": "t"},
            deadline=time.monotonic() + 20,
        ))

        self.assertTrue(budgets, "no request was made")
        for given, cap in budgets:
            self.assertLessEqual(
                cap - given, 1,
                f"a request clipped to {given}s could be held {cap}s, past a"
                " tail that is under five seconds on a short budget",
            )

    def test_an_s3_request_without_a_deadline_keeps_its_own_backstop(self):
        self.server.code = 500

        budgets = self._budgets(lambda: s3_mod.try_s3_upload(
            self.url, "test-key-000000",
            {"device_id": "D", "tools": [{"name": "T"}]}, {"phase": "t"},
        ))

        self.assertTrue(budgets, "no request was made")
        self.assertEqual([cap - given for given, cap in budgets], [5] * len(budgets))

    def test_an_s3_upload_without_a_deadline_keeps_its_own_timeouts(self):
        """try_s3_upload gained a deadline; a caller passing four positionals must
        still get the module's own budget, not a clipped one."""
        self.server.code = 500   # refuses the presign, so it stops after step one

        used = self._max_times(lambda: s3_mod.try_s3_upload(
            self.url, "test-key-000000",
            {"device_id": "D", "tools": [{"name": "T"}]}, {"phase": "t"},
        ))

        self.assertEqual(used, [s3_mod.META_TIMEOUT_SECONDS])

    def test_an_s3_upload_with_a_deadline_is_clipped_to_it(self):
        """The counterweight, so the test above cannot pass by accident: the same
        call with four seconds left asks curl for four, not thirty."""
        self.server.code = 500

        used = self._max_times(lambda: s3_mod.try_s3_upload(
            self.url, "test-key-000000",
            {"device_id": "D", "tools": [{"name": "T"}]}, {"phase": "t"},
            deadline=time.monotonic() + 4,
        ))

        self.assertEqual(len(used), 1)
        self.assertLessEqual(used[0], 4)

    def test_the_metrics_post_without_a_deadline_keeps_its_own_budget(self):
        used = self._max_times(lambda: utils_mod.send_discovery_metrics(
            self.url, "test-key-000000", "D", {"t": 1},
        ))

        self.assertEqual(used, [utils_mod.METRICS_TIMEOUT_SECONDS])

    def test_the_metrics_post_is_clipped_to_the_run_budget(self):
        """It runs immediately before the completed event, which the backend needs
        for its prune manifest. Unbounded, it can spend the run's last seconds and
        the event never goes out at all."""
        used = self._max_times(lambda: utils_mod.send_discovery_metrics(
            self.url, "test-key-000000", "D", {"t": 1},
            deadline=time.monotonic() + 3,
        ))

        self.assertEqual(len(used), 1)
        self.assertLessEqual(used[0], 3)

    def test_the_metrics_post_cannot_outlive_its_deadline_by_the_tail(self):
        """The last of the deadline-clipped sends to get this. Its backstop sat
        five seconds past a tail that can be under five on a short budget, so a
        curl ignoring --max-time outlived the window the completed event needs."""
        budgets = self._budgets(lambda: utils_mod.send_discovery_metrics(
            self.url, "test-key-000000", "D", {"t": 1},
            deadline=time.monotonic() + 8,
        ))

        self.assertTrue(budgets, "no request was made")
        for given, cap in budgets:
            self.assertLessEqual(
                cap - given, 1,
                f"a request clipped to {given}s could be held {cap}s, past the"
                " tail the completed event is holding",
            )

    def test_the_metrics_post_without_a_deadline_keeps_its_own_backstop(self):
        budgets = self._budgets(lambda: utils_mod.send_discovery_metrics(
            self.url, "test-key-000000", "D", {"t": 1},
        ))

        self.assertTrue(budgets, "no request was made")
        self.assertEqual([cap - given for given, cap in budgets], [5] * len(budgets))

    def test_the_s3_put_helper_still_takes_two_arguments(self):
        """_curl_put_to_s3 gained a defaulted timeout; the old two-argument call
        has to keep working and ask curl for the module's upload budget."""
        used = self._max_times(
            lambda: s3_mod._curl_put_to_s3(self.url, '{"a":1}')
        )

        self.assertEqual(used, [s3_mod.UPLOAD_TIMEOUT_SECONDS])

    def test_the_queue_writer_still_merges_when_called_the_old_way(self):
        save_failed_reports([{"id": 1}])
        save_failed_reports([{"id": 2}])

        self.assertEqual([e["report"]["id"] for e in
                          json.loads(self.queue_file.read_text())], [1, 2])


class ThePermanentRefusalsMatchWhatTheBackendSends(_ServesABackend):
    """The codes the discovery endpoints actually return, checked against how the
    client treats them. report_ai_tools returns 202, 400, 413 and 500."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}

    def test_an_oversized_report_is_refused_once_but_kept(self):
        """413 is about this endpoint, not the report: a body too large for the
        legacy route is what the S3 route exists for, and that is what the report
        tries first on the next run. So stop asking, but keep it."""
        self.server.code = 413

        with patch("time.sleep", Mock()):
            ok, retryable = send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT),
            )

        self.assertEqual(
            (ok, retryable), (False, True),
            "a report the legacy endpoint cannot take was discarded, so its data"
            " never reaches the backend even though S3 could carry it",
        )
        self.assertEqual(len(self.server.bodies), 1,
                         "asking the same endpoint again cannot change the answer")

    def test_a_server_error_is_still_retried(self):
        """The counterweight: 500 is the one the backend sends for a transient
        failure, and it has to keep its whole curve."""
        self.server.code = 500

        with patch("time.sleep", Mock()):
            ok, retryable = send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT),
            )

        self.assertEqual((ok, retryable), (False, True))
        self.assertEqual(len(self.server.bodies), utils_mod.MAX_ATTEMPTS)

    def test_a_validation_failure_is_refused_once(self):
        self.server.code = 400

        with patch("time.sleep", Mock()):
            ok, retryable = send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT),
            )

        self.assertEqual((ok, retryable), (False, False))
        self.assertEqual(len(self.server.bodies), 1)

    def test_the_dedup_accepted_response_counts_as_delivered(self):
        """202 is what the backend answers when it already holds this payload."""
        self.server.code = 202

        ok, retryable = send_report_to_backend(
            self.url, "test-key-000000", dict(self.REPORT),
        )

        self.assertEqual((ok, retryable), (True, False))


class _RunsTheWholeS3Flow(BaseHTTPRequestHandler):
    """Presign and PUT succeed; the notification answers whatever the test says.
    The object is in S3 by then, so what the notification says decides whether
    the backend may already hold this payload."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if "upload-url" in self.path:
            body = json.dumps({
                "upload_url": f"http://127.0.0.1:{self.server.server_address[1]}/put",
                "object_key": "k",
            }).encode()
            self._reply(200, body)
        elif "from-s3" in self.path:
            self.server.notified = True
            self._reply(self.server.notify_code, b'{"error":"no"}')
        else:
            # The legacy endpoint the sender falls back to once S3 has failed.
            self._reply(self.server.legacy_code, b'{"error":"no"}')

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._reply(200, b"")

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


class AStoredObjectWhoseNotificationFailsIsNotNothing(unittest.TestCase):
    """Once the PUT has landed, the payload exists in S3 and the backend may pick
    it up. A server error on the notification therefore has to be read as "it may
    be there", or the caller clears nothing and detection goes on trusting a hash
    that no longer describes the row."""

    def setUp(self):
        # The legacy fallback below reports its refusal, so the DSN has to go or
        # the test talks to Sentry for real.
        utils_mod._SENTRY_DSN = ""
        s3_mod.logger.disabled = True
        self.addCleanup(setattr, s3_mod.logger, "disabled", False)
        self.server = HTTPServer(("127.0.0.1", 0), _RunsTheWholeS3Flow)
        self.server.notify_code = 500
        self.server.legacy_code = 500
        self.server.notified = False
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _upload(self):
        failure = {}
        ok, _ = s3_mod.try_s3_upload(
            self.url, "test-key-000000",
            {"device_id": "D", "home_user": "u", "tools": [{"name": "T"}]},
            {"phase": "t"}, failure=failure,
        )
        self.assertFalse(ok)
        self.assertTrue(self.server.notified, "the flow never reached the notification")
        return failure

    def test_a_server_error_on_the_notification_is_uncertain(self):
        self.assertEqual(
            self._upload().get("kind"), "uncertain",
            "the object is in S3 and the backend answered 500, so it may have"
            " ingested it -- read as 'never arrived', the stale hash survives",
        )

    def test_a_lost_notification_does_not_mask_an_oversized_body(self):
        """The stored object may already have been ingested, so a refusal from
        the legacy fallback afterwards must not erase that: the report is kept
        either way, and the pair's cached hash can no longer be trusted."""
        self.server.legacy_code = 413
        failure = {}
        with patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                self.url, "test-key-000000",
                {"device_id": "D", "home_user": "u", "tools": [{"name": "T"}]},
                failure=failure,
            )

        self.assertEqual(failure.get("kind"), "uncertain",
                         "the stored object may have been ingested")

    def test_a_refusal_on_the_notification_is_not(self):
        """The counterweight: a 4xx is the backend declining to ingest it, which
        is not a maybe. Calling that uncertain would clear hashes for nothing."""
        self.server.notify_code = 400

        self.assertIsNone(self._upload().get("kind"))


class _Clock:
    """A clock the test advances itself, so a budget can be spent without
    spending any real time."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


class _SweepBackend(BaseHTTPRequestHandler):
    """Answers the sweep's worklist fetch and its per-connector reports."""

    def do_GET(self):
        self._json(json.dumps({"uuids": self.server.uuids}).encode())

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.reports += 1
        self._json(b'{"ok": true}')

    def _json(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


class TheInFlightMarkMatchesWhatIsOnTheWire(_ServesABackend):
    """A caller on another thread decides whether the backend may hold a report
    from this dict alone, and it can read it at any instant. So at every instant
    the dict has to match what is actually happening: a wait between attempts is
    not a request out there, and an attempt's outcome has to be published before
    that wait begins, or a reader takes the previous attempt's for it."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}

    def test_between_attempts_nothing_is_in_flight_and_the_outcome_is_published(self):
        self.server.code = 500
        failure, seen = {}, []
        real_backoff = utils_mod._backoff

        def _look(attempt, deadline=None):
            # Entered once per retry decision, on the sending thread, after the
            # attempt resolved and before the wait: exactly what an aborting
            # thread would see between two attempts.
            #
            # Hooked here rather than on time.sleep, which subprocess.run also
            # uses to poll its own wait -- snapshots from there come from the
            # middle of a request, where in-flight is true for good reason.
            seen.append(dict(failure))
            return real_backoff(attempt, deadline)

        with patch.object(utils_mod, "_backoff", _look), patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT), failure=failure,
            )

        self.assertEqual(
            len(seen), utils_mod.MAX_ATTEMPTS - 1,
            "a retry decision was not observed, so the gaps between attempts"
            f" were not all checked: {seen}",
        )
        for n, snapshot in enumerate(seen, start=1):
            self.assertFalse(
                snapshot.get("in_flight"),
                f"wait {n}: a pause between attempts looked like a request still"
                " out there, so an abort there says nothing and the backend keeps"
                " a run that never ends",
            )
            self.assertEqual(
                snapshot.get("kind"), "rejected",
                f"wait {n}: the attempt's outcome was not published before the"
                " wait, so a reader takes the attempt before it for this one",
            )

    def test_a_delivered_report_says_so_and_is_not_in_flight(self):
        failure = {}

        utils_mod.send_report_to_backend(
            self.url, "test-key-000000", dict(self.REPORT), failure=failure,
        )

        self.assertEqual(failure.get("kind"), "delivered")
        self.assertFalse(failure.get("in_flight"))


# Where a Sentry event goes, so a spy can tell one from a report send.
_STORE_PATH = "/api/1/store/"


class FailureTelemetryStaysInsideTheDeadline(_ServesABackend):
    """Each Sentry event is a request of its own, taken after the send that
    failed has already spent its budget. A caller working to a deadline never
    budgeted for it, so unbounded it comes out of the time detection and the
    completed event still need."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}

    def setUp(self):
        super().setUp()
        # Events are deduped by signature for the life of a run, so without this
        # the second case here files nothing and proves nothing.
        utils_mod.reset_sentry_run_state()
        self.addCleanup(utils_mod.reset_sentry_run_state)

    def _events(self, deadline):
        """The --max-time each Sentry POST was handed.

        Real curl to a real endpoint: the DSN keeps its scheme through the
        parser, so an http one aimed at the backend already serving these tests
        gives the event somewhere to go. The spy records and calls through."""
        seen = []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and any(_STORE_PATH in str(c) for c in cmd):
                seen.append(int(cmd[cmd.index("--max-time") + 1]))
            return real_run(cmd, *a, **kw)

        self.server.code = 500
        port = self.server.server_address[1]
        with patch.object(utils_mod, "_SENTRY_DSN", f"http://k@127.0.0.1:{port}/1"), \
             patch.object(utils_mod.subprocess, "run", _spy), \
             patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT), deadline=deadline,
            )
        return seen

    def test_an_event_is_clipped_to_what_the_run_has_left(self):
        seen = self._events(time.monotonic() + 2)

        self.assertTrue(seen, "the failed send filed no event")
        for given in seen:
            self.assertLessEqual(
                given, 2,
                f"an event was given {given}s inside a 2s remainder, so"
                " telemetry spends what detection and the completion need",
            )

    def test_without_a_deadline_an_event_keeps_its_own_budget(self):
        seen = self._events(None)

        self.assertTrue(seen, "the failed send filed no event")
        self.assertEqual(
            set(seen), {utils_mod.SENTRY_EVENT_TIMEOUT_SECONDS},
            "nothing to stay inside, so the event keeps the budget it had",
        )


class DecliningAnEventForLackOfTimeCostsNothing(_ServesABackend):
    """The bound on telemetry has to be decided before anything is spent on the
    event. Charged late, a skip burns the signature -- so that same failure can
    never be reported -- spends one of the run's events, and counts against the
    breaker, which turns Sentry off for the rest of the run after three.

    Real curl to the localhost endpoint already serving these tests, answering
    500 so a real attempt is a real failure to count. The spy records which
    attempts happen and calls through."""

    def setUp(self):
        super().setUp()
        utils_mod.reset_sentry_run_state()
        self.addCleanup(utils_mod.reset_sentry_run_state)
        self.calls = []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and any(_STORE_PATH in str(c) for c in cmd):
                self.calls.append(cmd)
            return real_run(cmd, *a, **kw)

        self.server.code = 500
        port = self.server.server_address[1]
        self._patches = [
            patch.object(utils_mod, "_SENTRY_DSN", f"http://k@127.0.0.1:{port}/1"),
            patch.object(utils_mod.subprocess, "run", _spy),
        ]
        for pt in self._patches:
            pt.start()
            self.addCleanup(pt.stop)

    def test_a_skipped_event_does_not_burn_its_signature(self):
        """Otherwise the same failure is deduped away for the rest of the run,
        and the one time it mattered is the time it was skipped."""
        err = RuntimeError("boom")
        utils_mod.report_to_sentry(err, {"phase": "p"}, max_seconds=0)
        self.assertEqual(self.calls, [], "it tried to send with no time left")

        utils_mod.report_to_sentry(err, {"phase": "p"}, max_seconds=None)

        self.assertEqual(
            len(self.calls), 1,
            "the skipped event recorded its signature, so the same failure was"
            " never reported once there was time for it",
        )

    def test_a_skipped_event_does_not_count_against_the_breaker(self):
        """Two real failures and one skip would otherwise be three, and Sentry
        goes dark for the rest of the run -- including the abort report."""
        for i in range(utils_mod._SENTRY_BREAKER_THRESHOLD - 1):
            utils_mod.report_to_sentry(RuntimeError("real"), {"phase": f"r{i}"})
        before = utils_mod._sentry_consecutive_fails

        utils_mod.report_to_sentry(RuntimeError("skipped"), {"phase": "s"},
                                   max_seconds=0)

        self.assertEqual(
            utils_mod._sentry_consecutive_fails, before,
            "a skip counted as the transport failing, so the breaker trips one"
            " event early and the run reports nothing further",
        )
        self.assertFalse(utils_mod._sentry_dead_this_run)

    def test_a_skipped_event_does_not_spend_one_of_the_run_s_events(self):
        before = utils_mod._sentry_event_count

        utils_mod.report_to_sentry(RuntimeError("boom"), {"phase": "p"},
                                   max_seconds=0)

        self.assertEqual(utils_mod._sentry_event_count, before,
                         "the run was charged for an event it never sent")


class AQueueRewriteIsAllOrNothing(unittest.TestCase):
    """The queue is rewritten several times during a run -- after the drain, and
    again whenever an upload supersedes a backlog entry -- and the watchdog can
    end the process between any two instructions. A half-written file loads as
    nothing, so writing in place would cost the whole backlog rather than one
    rewrite."""

    def setUp(self):
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="queue-atomic-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()
        utils_mod._SENTRY_DSN = ""

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def _owed(self, n):
        return [{"device_id": f"Q-{i}", "home_user": "u", "tools": []}
                for i in range(n)]

    def test_a_rewrite_cut_short_leaves_the_previous_queue_readable(self):
        utils_mod.save_failed_reports(self._owed(3), replace=True)
        before = self.queue_file.read_bytes()

        # The process ending mid-rewrite, from the queue file's point of view.
        with patch.object(utils_mod.os, "replace", side_effect=OSError("killed")):
            utils_mod.save_failed_reports(self._owed(1), replace=True)

        self.assertEqual(
            self.queue_file.read_bytes(), before,
            "the destination was written over rather than swapped, so a kill"
            " mid-write leaves a partial file that loads as nothing",
        )
        self.assertEqual(
            len(utils_mod.load_pending_reports()), 3,
            "the backlog did not survive an interrupted rewrite",
        )

    def test_a_rewrite_that_completes_leaves_no_temporary_behind(self):
        utils_mod.save_failed_reports(self._owed(2), replace=True)

        leftovers = [q.name for q in Path(self._dir).iterdir()
                     if q.name != "queue.json"]
        self.assertEqual(leftovers, [], f"temporary files left in place: {leftovers}")
        self.assertEqual(len(utils_mod.load_pending_reports()), 2)


class TheProcessBackstopStaysInsideTheDeadline(_ServesABackend):
    """curl's own --max-time is what should end a request; the timeout on
    subprocess.run behind it is only a backstop for curl ignoring that. On a
    short budget the end-of-run tail can be under five seconds, so a backstop
    five seconds past the deadline leaves the call running when the watchdog
    fires -- over an event the backend may already have taken."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}

    def _caps(self, deadline):
        """The --max-time and process cap curl was handed, per attempt."""
        seen = []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd:
                seen.append((int(cmd[cmd.index("--max-time") + 1]), kw.get("timeout")))
            return real_run(cmd, *a, **kw)

        with patch.object(utils_mod.subprocess, "run", _spy), \
             patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT), deadline=deadline,
            )
        return seen

    def test_a_bounded_send_cannot_outlive_its_deadline_by_the_tail(self):
        seen = self._caps(time.monotonic() + 20)

        self.assertTrue(seen, "no request was made")
        for given, cap in seen:
            self.assertLessEqual(
                cap - given, 1,
                f"a request clipped to {given}s could be held {cap}s, past a"
                " tail that is under five seconds on a short budget",
            )

    def test_an_unbounded_send_keeps_its_own_backstop(self):
        """Nothing to stay inside, so curl keeps the slack it always had."""
        seen = self._caps(None)

        self.assertTrue(seen, "no request was made")
        self.assertEqual([cap - given for given, cap in seen], [5] * len(seen))


class TheConnectorSweepStaysInsideTheRunBudget(unittest.TestCase):
    """The sweep runs after the outcome is reported, so a run with a long
    connector list could keep the process alive well past the budget it was
    given: its worklist is one 60s request per connector, after a 45s fetch.

    Real curl against a real localhost backend; what the test controls is the
    clock the sweep reads, so a budget can be spent without spending it."""

    UUIDS = ["uuid-1", "uuid-2", "uuid-3"]

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _SweepBackend)
        self.server.uuids = list(self.UUIDS)
        self.server.reports = 0
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.local = {u: {"name": f"n{i}", "tools": []}
                      for i, u in enumerate(self.UUIDS)}

    def _run(self, deadline_in=None, cost=0.0):
        """Really sweep, recording the --max-time curl was handed each time. The
        clock advances by `cost` per request, standing in for the time a real
        one takes against a backend that is not on localhost."""
        clock, seen, caps = _Clock(), [], []
        real_run = sweep_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd:
                seen.append(int(cmd[cmd.index("--max-time") + 1]))
                caps.append(kw.get("timeout"))
                clock.now += cost
            return real_run(cmd, *a, **kw)

        deadline = None if deadline_in is None else clock.now + deadline_in
        with patch.object(sweep_mod, "time", clock), \
             patch.object(sweep_mod.subprocess, "run", _spy), \
             patch.object(sweep_mod, "read_local_connectors", return_value=self.local):
            result = sweep_mod.run_sweep(self.url, "test-key-000000", deadline=deadline)
        self.caps = caps
        return seen, result

    def test_the_worklist_stops_once_the_budget_is_spent(self):
        """Two of three connectors fit; the third has no room left. The second
        one's request is clipped on the way, which is the other half of the rule."""
        seen, (sent, _failed, matched) = self._run(deadline_in=120, cost=40)

        self.assertEqual(
            seen, [30, 60, 40],
            "the sweep worked through its whole list regardless of what the run"
            " had left, keeping the process alive past its budget",
        )
        self.assertEqual((sent, matched), (2, 3), "it claimed work it never did")
        self.assertEqual(self.server.reports, 2, "the backend heard otherwise")

    def test_each_request_is_clipped_to_what_the_run_has_left(self):
        seen, _ = self._run(deadline_in=5)

        self.assertEqual(seen[0], 5, "the fetch took its own ceiling rather than"
                                     " what the run had left")
        self.assertTrue(all(t <= 5 for t in seen), f"a request overran: {seen}")

    def test_nothing_is_started_with_no_budget_left(self):
        seen, result = self._run(deadline_in=0)

        self.assertEqual(seen, [], "the sweep started work the run had no time for")
        self.assertEqual(result, (0, 0, 0))

    def test_a_hung_request_cannot_hold_the_run_much_past_the_deadline(self):
        """curl's own timeout is what should end a request; the process-level cap
        is only a backstop for it not doing so, and the deadline is only as real
        as the longest a single request can hold it."""
        seen, _ = self._run(deadline_in=120, cost=40)

        for given, cap in zip(seen, self.caps):
            self.assertLessEqual(
                cap, given + sweep_mod.CURL_KILL_GRACE,
                f"a request clipped to {given}s could be held {cap}s, well past"
                " what the run had left",
            )

    def test_no_deadline_leaves_the_ceilings_alone(self):
        """The standalone entry point has no run to stay inside."""
        seen, _ = self._run()

        self.assertEqual(seen[0], sweep_mod.LIST_MAX_TIME)
        self.assertEqual(seen[1:], [sweep_mod.REPORT_MAX_TIME] * 3)


class ADroppedPacketIsAsConclusiveAsARefusedOne(unittest.TestCase):
    """A VPN that is down or a firewall that blackholes the backend does not
    refuse the connection -- it swallows it, and curl reports the same timeout a
    slow server would. Read as "it may have arrived", every queued report pays a
    full retry curve against a host that was never there.

    A localhost server cannot drop a handshake: filling its accept queue
    completes the connection instead, differently on each platform. So the rule
    is split -- the outcome end to end over a refused localhost port, and the
    discriminator itself against the exit code and connection count curl really
    produces. The stalled-server case below supplies its other half over real
    traffic."""

    REPORT = {"device_id": "D", "home_user": "u", "tools": []}
    # Nothing listens, so curl is refused without ever getting a connection.
    REFUSED = "http://127.0.0.1:1"
    CONNECT_TIMEOUT = 3

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def _send(self, domain):
        """Really send to `domain` and report what curl was asked and what it said."""
        calls, results = [], []
        real_run = utils_mod.subprocess.run

        def _spy(cmd, *a, **kw):
            calls.append(cmd)
            result = real_run(cmd, *a, **kw)
            results.append(result)
            return result

        failure = {}
        with patch.object(utils_mod.subprocess, "run", _spy), \
             patch.object(utils_mod, "CONNECT_TIMEOUT_SECONDS", self.CONNECT_TIMEOUT), \
             patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                domain, "test-key-000000", dict(self.REPORT),
                failure=failure, stop_if_unreachable=True,
            )
        return failure, calls, results

    def test_a_host_that_was_never_reached_settles_the_queue(self):
        """End to end: one attempt, and the queue behind it is left alone."""
        failure, calls, results = self._send(self.REFUSED)

        self.assertEqual(failure.get("kind"), "unreachable")
        self.assertEqual(len(calls), 1,
                         "a host that was never reached was asked again")
        self.assertEqual(
            [r.stdout.strip().rpartition("\n")[2].partition(" ")[0] for r in results],
            ["0"], "curl reported a connection to a port nothing listens on",
        )

    def test_a_timeout_without_a_connection_is_not_a_timeout_in_flight(self):
        """The one combination localhost cannot produce. curl exits 28 both for a
        blackholed host and for a server that accepts and then says nothing; the
        connection count is the whole difference, and reading it wrong is what
        made every queued report pay a full retry curve."""
        self.assertTrue(utils_mod.never_connected(28, "0"))
        self.assertFalse(
            utils_mod.never_connected(28, "1"),
            "a request that went out would be abandoned as if it never had",
        )

    def test_a_refusal_is_conclusive_whatever_it_writes_out(self):
        """Exit 6 and 7 settle it on their own, so a lost write-out cannot turn a
        refusal into a maybe."""
        self.assertTrue(utils_mod.never_connected(7, ""))
        self.assertTrue(utils_mod.never_connected(6, ""))

    def test_the_connect_phase_is_bounded(self):
        """A blackholed host would otherwise hold each attempt for the whole
        request budget before curl gave up on it."""
        _, calls, _ = self._send(self.REFUSED)

        self.assertIn("--connect-timeout", calls[0])
        self.assertIn(str(self.CONNECT_TIMEOUT), calls[0])


class AConnectedSendThatTimesOutIsStillAMaybe(_ServesABackend):
    """The counterweight to the blackhole case, over real traffic: curl connects,
    the server never answers, and the attempt times out with the same exit code a
    dropped packet gives. The request did go out, so the backend may hold it and
    the pair's hash cannot be trusted."""

    def test_a_server_that_never_answers_leaves_the_outcome_uncertain(self):
        self.server.stall = 30
        failure = {}

        with patch("time.sleep", Mock()):
            utils_mod.send_report_to_backend(
                self.url, "test-key-000000",
                {"device_id": "D", "home_user": "u", "tools": []},
                failure=failure, stop_if_unreachable=True,
                deadline=time.monotonic() + 2,
            )

        self.assertEqual(
            failure.get("kind"), "uncertain",
            "a request that was answered by nothing is not a request that never"
            " went: reading it as unreachable leaves the stale hash in place",
        )
        self.assertEqual(len(self.server.bodies), 1, "the backend did receive it")


class TheFormatsSurviveARollback(_ServesABackend):
    """The queue and cache files on a device outlive a release. A rollback has to
    read what the new code wrote, and the new code has to read what the old one
    left behind."""

    def test_the_new_code_reads_an_envelope_the_old_code_wrote(self):
        stamp = datetime.now(timezone.utc).isoformat()
        self.queue_file.write_text(json.dumps(
            [{"report": {"device_id": "OLD"}, "queued_at": stamp}]
        ))

        self.assertEqual(load_pending_reports(), [{"device_id": "OLD"}])

    def test_the_new_code_reads_a_bare_report_with_no_envelope(self):
        """The oldest shape on disk: a report that was never wrapped."""
        self.queue_file.write_text(json.dumps([{"device_id": "ANCIENT"}]))

        self.assertEqual(load_pending_reports(), [{"device_id": "ANCIENT"}])

    def test_what_the_new_writer_produces_is_the_same_envelope_shape(self):
        """A rollback reads this file with the old loader, which wants exactly
        `report` and `queued_at` and nothing else."""
        save_failed_reports([{"device_id": "NEW"}], replace=True)

        [envelope] = json.loads(self.queue_file.read_text())
        self.assertEqual(set(envelope), {"report", "queued_at"})
        datetime.fromisoformat(envelope["queued_at"])   # parses as it always did

    def test_forgetting_a_hash_leaves_a_cache_an_older_build_can_read(self):
        """forget_tool_hash removes a user key. An older build reading that cache
        must see a miss and re-upload, not trip over a hole."""
        cache = {"tools": {"Claude Code": {"alice": {"payload_hash": "h1"},
                                           "bob": {"payload_hash": "h2"}}}}
        written = {}
        with patch.object(cache_mod, "read_cache", return_value=cache), \
             patch.object(cache_mod, "atomic_write_cache",
                          side_effect=lambda c: written.update(c)):
            cache_mod.forget_tool_hash("Claude Code", "alice")

        self.assertIsNone(cache_mod.get_cached_hash("Claude Code", "alice", written))
        self.assertEqual(cache_mod.get_cached_hash("Claude Code", "bob", written), "h2")

    def test_the_wire_payload_gained_no_keys(self):
        """The deadline work is client-side. A key appearing here means the
        backend contract moved and ingestion has to be considered."""
        send_report_to_backend(
            self.url, "test-key-000000",
            {"device_id": "D", "home_user": "u", "tools": [], "run_id": "r"},
            "JumpCloud",
        )

        unexpected = set(self.sent_bodies()[0]) - REPORT_KEYS
        self.assertEqual(unexpected, set(), f"new keys on the wire: {unexpected}")


class TheDrainSurvivesADegradedDevice(_ServesABackend):
    """What a real device throws at it: a queue it cannot write, a queue file
    something corrupted, and payloads big enough that rewriting costs."""

    def test_a_queue_path_it_cannot_write_is_reported_not_raised(self):
        """The writer creates the parent directory, so a parent that is already a
        file fails on every platform. chmod would not do: on Windows it only sets
        the read-only attribute and files inside the directory still write."""
        blocker = Path(self._dir) / "not-a-directory"
        blocker.write_text("i am a file")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(blocker / "queue.json")

        landed = save_failed_reports([{"device_id": "D"}], replace=True)

        self.assertFalse(landed, "an unwritable queue path reported success")

    def test_a_corrupt_queue_file_does_not_stop_the_run(self):
        for junk in (b"", b"{", b"not json at all", b'{"a": 1}', b"[1, 2, 3]"):
            with self.subTest(junk=junk):
                self.queue_file.write_bytes(junk)
                self.assertIsInstance(load_pending_reports(), list)

    def test_a_queue_of_large_payloads_is_rewritten_without_blowing_up(self):
        """A full queue of real-sized payloads: the authoritative rewrite reads and
        re-serialises every one of them."""
        big = "x" * (256 * 1024)
        reports = [{"device_id": f"D{i}", "home_user": "u",
                    "tools": [{"name": "T", "projects": [{"rules": big}]}]}
                   for i in range(utils_mod.MAX_QUEUE_SIZE)]

        self.assertTrue(save_failed_reports(reports, replace=True))
        self.assertEqual(len(load_pending_reports()), utils_mod.MAX_QUEUE_SIZE)


if __name__ == "__main__":
    unittest.main()
