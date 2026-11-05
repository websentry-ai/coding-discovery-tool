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
        seen = []
        real_run = s3_mod.subprocess.run

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list) and "--max-time" in cmd:
                seen.append(int(cmd[cmd.index("--max-time") + 1]))
            return real_run(cmd, *a, **kw)

        with patch.object(s3_mod.subprocess, "run", spy):
            call()
        return seen

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


class TheConnectorSweepStaysInsideTheRunBudget(unittest.TestCase):
    """The sweep runs after the outcome is reported, so nothing aborts it. Its
    worklist is as long as the device's connector list and each entry gets its own
    60s request, so without the run's deadline it can keep the process alive long
    past the budget the run was given."""

    THREE = {f"uuid-{i}": {"name": f"n{i}", "tools": []} for i in (1, 2, 3)}

    def test_the_worklist_stops_once_the_budget_is_spent(self):
        clock, reported = _Clock(), []

        def _report(_domain, _api_key, uuid, _name, _tools, max_time=None):
            reported.append(max_time)
            clock.now += 40          # as a real report can
            return ("200", "")

        with patch.object(sweep_mod, "time", clock), \
             patch.object(sweep_mod, "fetch_unresolved_uuids",
                          return_value=list(self.THREE)), \
             patch.object(sweep_mod, "read_local_connectors", return_value=self.THREE), \
             patch.object(sweep_mod, "report_connector", _report):
            sent, failed, matched = sweep_mod.run_sweep(
                "http://127.0.0.1:1", "k", deadline=clock.now + 70
            )

        self.assertEqual(
            reported, [60, 30],
            "the sweep worked through its list regardless of what the run had"
            " left, keeping the process alive past its budget",
        )
        self.assertEqual((sent, matched), (2, 3), "it claimed work it never did")

    def test_the_worklist_request_is_clipped_to_what_is_left(self):
        clock, seen = _Clock(), []

        def _fetch(_domain, _api_key, max_time=None):
            seen.append(max_time)
            return []

        with patch.object(sweep_mod, "time", clock), \
             patch.object(sweep_mod, "fetch_unresolved_uuids", _fetch):
            sweep_mod.run_sweep("http://127.0.0.1:1", "k", deadline=clock.now + 5)

        self.assertEqual(seen, [5], "the request took its own ceiling rather than"
                                    " what the run had left")

    def test_nothing_is_started_with_no_budget_left(self):
        clock, seen = _Clock(), []

        with patch.object(sweep_mod, "time", clock), \
             patch.object(sweep_mod, "fetch_unresolved_uuids",
                          side_effect=lambda *a, **k: seen.append(1) or []):
            sweep_mod.run_sweep("http://127.0.0.1:1", "k", deadline=clock.now)

        self.assertEqual(seen, [], "the sweep started work the run had no time for")

    def test_no_deadline_leaves_the_ceilings_alone(self):
        """The standalone entry point has no run to stay inside."""
        seen = []

        def _fetch(_domain, _api_key, max_time=None):
            seen.append(max_time)
            return []

        with patch.object(sweep_mod, "fetch_unresolved_uuids", _fetch):
            sweep_mod.run_sweep("http://127.0.0.1:1", "k")

        self.assertEqual(seen, [sweep_mod.LIST_MAX_TIME])


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
