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
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.cache as cache_mod
import scripts.coding_discovery_tools.s3_uploader as s3_mod
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
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        self._dir = tempfile.mkdtemp(prefix="drain-compat-")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = str(Path(self._dir) / "queue.json")
        self.queue_file = utils_mod._get_queue_file_path()

    def tearDown(self):
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

    def test_an_oversized_report_is_refused_once_and_discarded(self):
        """413 comes from a fixed body-size limit, so the same payload is refused
        every time. Retrying it costs a cycle and then a day of re-sends."""
        self.server.code = 413

        with patch("time.sleep", Mock()):
            ok, retryable = send_report_to_backend(
                self.url, "test-key-000000", dict(self.REPORT),
            )

        self.assertEqual(
            (ok, retryable), (False, False),
            "an oversized report was marked retryable, so it goes back on the queue",
        )
        self.assertEqual(len(self.server.bodies), 1, "it was sent more than once")

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
