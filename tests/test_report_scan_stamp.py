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

from scripts.coding_discovery_tools import ai_tools_discovery
from scripts.coding_discovery_tools import utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector

_PRIOR_DSN = None


def setUpModule():
    global _PRIOR_DSN
    _PRIOR_DSN = utils_mod._SENTRY_DSN
    utils_mod._SENTRY_DSN = ""


def tearDownModule():
    utils_mod._SENTRY_DSN = _PRIOR_DSN


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
    """Data reports go to S3 first, and the backend answers an unchanged report
    from the notification alone -- before the blob is ever read."""

    def test_the_notify_body_includes_it(self):
        source = (
            __import__('pathlib').Path(ai_tools_discovery.__file__).parent
            / "s3_uploader.py"
        ).read_text()
        notify = source[source.index("notify_body = {"):]
        notify = notify[:notify.index("}")]

        self.assertIn(
            '"scanned_at"', notify,
            "the notification omits the snapshot time, so an unchanged S3 report "
            "cannot move the ordering clock",
        )

    def test_the_builder_supplies_what_the_notification_forwards(self):
        """The notification reads it off the payload, so the payload must have it."""
        self.assertIn("scanned_at", _report())

if __name__ == "__main__":
    unittest.main()
