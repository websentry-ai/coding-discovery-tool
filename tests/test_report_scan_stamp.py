"""The report carries when its snapshot was taken.

A queued report goes out on a later run, so without a stamp for the snapshot
itself a late delivery is indistinguishable from a fresh one. It is taken at
build time and has to survive the queue unchanged, which is what these assert.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from scripts.coding_discovery_tools import ai_tools_discovery
from scripts.coding_discovery_tools import s3_uploader
from scripts.coding_discovery_tools import utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector

_PRIOR_DSN = None


def setUpModule():
    global _PRIOR_DSN
    _PRIOR_DSN = utils_mod._SENTRY_DSN
    utils_mod._SENTRY_DSN = ""


def tearDownModule():
    utils_mod._SENTRY_DSN = _PRIOR_DSN


class _CapturingBackend(BaseHTTPRequestHandler):
    """Serves the three steps and keeps whatever the notification sent."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path.endswith("/upload-url/"):
            body = json.dumps({
                "upload_url": f"http://127.0.0.1:{self.server.port}/put",
                "object_key": "reports/test.json",
            }).encode()
        else:
            try:
                self.server.notified.append(json.loads(raw))
            except ValueError:
                self.server.notified.append({"unparsed": True})
            body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_a):
        pass


def _report(tool_name="dummy-tool"):
    return AIToolsDetector().generate_single_tool_report(
        {"name": tool_name, "version": "1.0"},
        device_id="DEV-123",
        home_user="testuser",
    )


class TheReportSaysWhenItWasTaken(unittest.TestCase):

    def test_every_report_carries_a_stamp(self):
        self.assertIn(
            "scanned_at", _report(),
            "ingestion has nothing to order by without this field",
        )

    def test_the_stamp_is_an_absolute_instant(self):
        """A naive local time is unorderable across devices and DST changes."""
        parsed = datetime.fromisoformat(_report()["scanned_at"])

        self.assertIsNotNone(
            parsed.tzinfo, "the stamp carries no offset, so it names no instant")
        self.assertLess(
            abs(parsed - datetime.now(timezone.utc)), timedelta(minutes=5),
            "the stamp is nowhere near now, so it does not describe this scan",
        )

    def test_a_later_scan_stamps_later(self):
        """Ordering is the only property ingestion relies on."""
        first = datetime.fromisoformat(_report()["scanned_at"])
        second = datetime.fromisoformat(_report()["scanned_at"])

        self.assertLessEqual(first, second)


class AQueuedReportKeepsItsOwnStamp(unittest.TestCase):
    """The case the stamp exists for: a report delivered a day late still
    describes the day-old state, and must not pass for current."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="scan-stamp-queue-")
        self._prior = os.environ.get("AI_DISCOVERY_QUEUE_FILE")
        os.environ["AI_DISCOVERY_QUEUE_FILE"] = os.path.join(self._dir, "queue.json")
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        if self._prior is None:
            os.environ.pop("AI_DISCOVERY_QUEUE_FILE", None)
        else:
            os.environ["AI_DISCOVERY_QUEUE_FILE"] = self._prior
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_the_stamp_is_unchanged_by_the_round_trip(self):
        original = _report()

        utils_mod.save_failed_reports([original])
        drained = utils_mod.load_pending_reports()

        self.assertEqual(len(drained), 1)
        self.assertEqual(
            drained[0].get("scanned_at"), original["scanned_at"],
            "the queue rewrote the stamp, so a late delivery would look fresh",
        )

    def test_a_drained_report_is_older_than_one_taken_after_it(self):
        """What ingestion has to be able to tell apart.

        The earlier scan's clock is pinned rather than relying on time passing
        between the two builds: Windows moves the wall clock in ~16ms steps, so
        back-to-back calls can read identical and strict ordering would flake.
        """
        earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(ai_tools_discovery, "datetime") as clock:
            clock.now.return_value = earlier
            queued = _report()
        utils_mod.save_failed_reports([queued])

        fresh = _report()
        drained = utils_mod.load_pending_reports()[0]

        self.assertEqual(datetime.fromisoformat(drained["scanned_at"]), earlier)
        self.assertLess(
            datetime.fromisoformat(drained["scanned_at"]),
            datetime.fromisoformat(fresh["scanned_at"]),
            "the queued snapshot does not read as older than the current one",
        )


class TheFrozenWireRecordKnowsAboutTheStamp(unittest.TestCase):
    """The wire-key guard builds its own payload, so it is blind to a key the
    real builder adds. Checked against the builder here instead."""

    def test_every_key_the_builder_emits_is_recorded(self):
        from tests.test_drain_compat_and_surfaces import REPORT_KEYS

        unrecorded = set(_report()) - REPORT_KEYS

        self.assertEqual(
            unrecorded, set(),
            f"the report gained keys the frozen wire record does not list: {unrecorded}",
        )


class TheStampTravelsWithTheRestOfTheReport(unittest.TestCase):

    def test_it_survives_json_as_the_wire_does_it(self):
        report = _report()

        round_tripped = json.loads(json.dumps(report))

        self.assertEqual(round_tripped["scanned_at"], report["scanned_at"])

    def test_it_does_not_disturb_the_existing_fields(self):
        with patch.object(ai_tools_discovery, "in_container", return_value=False):
            report = _report()

        for field in ("home_user", "system_user", "device_id", "is_container", "tools"):
            self.assertIn(field, report)




class TheS3NotificationCarriesTheStamp(unittest.TestCase):
    """Data reports upload to S3 and then notify, and the backend answers an
    unchanged report from that notification alone -- before the blob is read.

    The real three-step flow is run against a local server: a source check would
    still pass if the value came from the wrong key, or arrived as None and were
    dropped by the None filter the notification applies.
    """

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _CapturingBackend)
        self.server.notified = []
        self.server.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.port}"

    def _upload(self, report):
        ok, _retryable = s3_uploader.try_s3_upload(self.url, "test-key-000000", report)
        self.assertTrue(ok, "the upload flow did not complete against the stub")
        self.assertEqual(len(self.server.notified), 1, "no notification arrived")
        return self.server.notified[0]

    def test_the_notification_carries_the_builder_s_stamp(self):
        report = _report()

        notify = self._upload(report)

        self.assertEqual(
            notify.get("scanned_at"), report["scanned_at"],
            "the notification did not carry the snapshot time the report was built with",
        )

    def test_the_stamp_is_not_dropped_as_empty(self):
        """The notification strips None values, so a wrong key would vanish silently."""
        notify = self._upload(_report())

        self.assertIn("scanned_at", notify)
        self.assertTrue(notify["scanned_at"])


if __name__ == "__main__":
    unittest.main()
