"""Tests for Auggie CLI (Augment) subscription plan detection.

Auggie stores no plan on disk, so the plan is read by running the tool's own
``auggie account status --json`` and parsing ``planName`` — mirroring how Claude
Code's plan is read. These tests mock ``subprocess.run`` and cover the happy path
plus every failure mode (non-zero exit, non-JSON, timeout, missing binary, and a
response without a plan).
"""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.utils import get_auggie_subscription_type


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


_OK_JSON = json.dumps({
    "planName": "Business Plan",
    "usageUnit": "usd",
    "amountRemaining": "95.85",
    "amountIncludedPerCycle": "100",
    "billingCycleEndDate": "2026-08-24T21:34:26Z",
    "daysRemainingInCycle": 17,
    "banner": None,
})


class TestGetAuggieSubscriptionType(unittest.TestCase):
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_parses_plan_name(self, mock_run):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        self.assertEqual(get_auggie_subscription_type("/usr/bin/auggie"), "Business Plan")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_runs_account_status_json(self, mock_run):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        get_auggie_subscription_type("/opt/homebrew/bin/auggie")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["/opt/homebrew/bin/auggie", "account", "status", "--json"])

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_none_binary_falls_back_to_path(self, mock_run):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        get_auggie_subscription_type(None)
        self.assertEqual(mock_run.call_args[0][0][0], "auggie")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_runs_for_own_home(self, mock_run):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        self.assertEqual(
            get_auggie_subscription_type("/usr/bin/auggie", Path.home()),
            "Business Plan",
        )
        mock_run.assert_called_once()

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_other_user_home_is_skipped(self, mock_run):
        # Never execute another user's binary / read their session during a
        # privileged multi-user scan.
        other = Path.home() / "definitely-not-the-scanning-users-home"
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", other))
        mock_run.assert_not_called()

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_plan_name_is_stripped(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"planName": "  Business Plan  "}))
        self.assertEqual(get_auggie_subscription_type("/usr/bin/auggie"), "Business Plan")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_missing_plan_name_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"amountRemaining": "10"}))
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_empty_plan_name_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"planName": "   "}))
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_non_zero_exit_returns_none(self, mock_run):
        # e.g. not logged in / "User has no subscription"
        mock_run.return_value = _proc(returncode=1, stderr="not authenticated")
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_non_json_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout="Business Plan (human text)")
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="auggie", timeout=15)
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    def test_missing_binary_returns_none(self, mock_run):
        mock_run.side_effect = FileNotFoundError("auggie not found")
        self.assertIsNone(get_auggie_subscription_type("/nope/auggie"))


if __name__ == "__main__":
    unittest.main()
