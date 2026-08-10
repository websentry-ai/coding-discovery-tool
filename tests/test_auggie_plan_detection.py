"""Tests for Auggie CLI (Augment) subscription plan detection.

Auggie stores no plan on disk, so the plan is read by running the tool's own
``auggie account status --json`` and parsing ``planName`` — mirroring how Claude
Code's plan is read. Because that means executing a CLI, the safety gate
(:func:`_resolve_auggie_binary_for_self_scan`) is tested separately from the
run/parse logic.
"""

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools import utils
from scripts.coding_discovery_tools.utils import (
    get_auggie_subscription_type,
    _resolve_auggie_binary_for_self_scan,
    _which_no_cwd,
)

_MOD = "scripts.coding_discovery_tools.utils"


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


_OK_JSON = json.dumps({"planName": "Business Plan", "usageUnit": "usd"})


class TestResolveBinaryForSelfScan(unittest.TestCase):
    """The safety gate: only our own session, never as root, absolute binary."""

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    def test_own_home_resolves_binary(self, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        got = _resolve_auggie_binary_for_self_scan("/home/alice/.local/bin/auggie",
                                                   Path("/home/alice"))
        self.assertEqual(got, os.path.abspath("/home/alice/.local/bin/auggie"))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._windows_process_is_elevated", return_value=False)
    def test_windows_own_home_resolves(self, _elev, _sys):
        got = _resolve_auggie_binary_for_self_scan("C:\\npm\\auggie.cmd", Path.home())
        self.assertEqual(got, os.path.abspath("C:\\npm\\auggie.cmd"))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._windows_process_is_elevated", return_value=True)
    def test_windows_elevated_refused(self, _elev, _sys):
        # An elevated (or unknown-elevation) scan must not exec a user-writable shim.
        self.assertIsNone(
            _resolve_auggie_binary_for_self_scan("C:\\npm\\auggie.cmd", Path.home()))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._windows_process_is_elevated", return_value=False)
    def test_windows_other_home_refused(self, _elev, _sys):
        other = Path.home() / "not-the-scanning-users-home"
        self.assertIsNone(
            _resolve_auggie_binary_for_self_scan("C:\\npm\\auggie.cmd", other))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    def test_other_user_home_refused(self, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        self.assertIsNone(
            _resolve_auggie_binary_for_self_scan("/bin/auggie", Path("/home/bob")))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=0, create=True)
    def test_root_refused(self, _euid, _sys):
        # $HOME could be spoofed to a user's home under sudo -E; refuse outright.
        self.assertIsNone(
            _resolve_auggie_binary_for_self_scan("/home/alice/.local/bin/auggie",
                                                 Path("/home/alice")))

    def test_none_user_home_refused(self):
        self.assertIsNone(_resolve_auggie_binary_for_self_scan("/bin/auggie", None))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    @patch(f"{_MOD}.shutil.which", return_value=None)
    def test_unresolvable_bare_name_refused(self, _which, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        self.assertIsNone(
            _resolve_auggie_binary_for_self_scan(None, Path("/home/alice")))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    @patch(f"{_MOD}.shutil.which", return_value="/usr/bin/auggie")
    def test_bare_name_resolved_to_absolute(self, _which, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        self.assertEqual(
            _resolve_auggie_binary_for_self_scan(None, Path("/home/alice")),
            os.path.abspath("/usr/bin/auggie"))


class TestGetAuggieSubscriptionType(unittest.TestCase):
    """Run/parse logic, with the safety gate stubbed to a fixed binary."""

    def setUp(self):
        p = patch(f"{_MOD}._resolve_auggie_binary_for_self_scan",
                  return_value="/usr/bin/auggie")
        self.addCleanup(p.stop)
        p.start()

    @patch(f"{_MOD}.subprocess.run")
    def test_parses_plan_name(self, mock_run):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        self.assertEqual(get_auggie_subscription_type("/usr/bin/auggie", Path.home()),
                         "Business Plan")

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.subprocess.run")
    def test_posix_runs_without_shell(self, mock_run, _sys):
        mock_run.return_value = _proc(stdout=_OK_JSON)
        get_auggie_subscription_type("/usr/bin/auggie", Path.home())
        self.assertEqual(mock_run.call_args[0][0],
                         ["/usr/bin/auggie", "account", "status", "--json"])
        self.assertFalse(mock_run.call_args.kwargs["shell"])

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}.subprocess.run")
    def test_windows_uses_shell(self, mock_run, _sys):
        # npm .cmd shim needs shell=True, matching the Codex/Copilot probes.
        mock_run.return_value = _proc(stdout=_OK_JSON)
        get_auggie_subscription_type("C:\\npm\\auggie.cmd", Path.home())
        self.assertTrue(mock_run.call_args.kwargs["shell"])

    @patch(f"{_MOD}.subprocess.run")
    def test_home_env_set_to_user_home(self, mock_run):
        # auggie reads ~/.augment via HOME; point it at the verified own home.
        mock_run.return_value = _proc(stdout=_OK_JSON)
        get_auggie_subscription_type("/usr/bin/auggie", Path("/home/alice"))
        self.assertEqual(mock_run.call_args.kwargs["env"]["HOME"], "/home/alice")

    @patch(f"{_MOD}.subprocess.run")
    def test_plan_name_is_stripped(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"planName": "  Business Plan  "}))
        self.assertEqual(get_auggie_subscription_type("/usr/bin/auggie", Path.home()),
                         "Business Plan")

    @patch(f"{_MOD}.subprocess.run")
    def test_oversized_plan_rejected(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"planName": "x" * 101}))
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))

    @patch(f"{_MOD}.subprocess.run")
    def test_control_char_plan_rejected(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"planName": "Bus\x00iness"}))
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))

    @patch(f"{_MOD}.subprocess.run")
    def test_missing_plan_name_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"usageUnit": "usd"}))
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))

    @patch(f"{_MOD}.subprocess.run")
    def test_non_zero_exit_returns_none_without_logging_stderr(self, mock_run):
        mock_run.return_value = _proc(returncode=1, stderr="token=abc123 session leak")
        with self.assertLogs(utils.logger, level="DEBUG") as cm:
            self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))
        self.assertFalse(any("abc123" in line for line in cm.output))

    @patch(f"{_MOD}.subprocess.run")
    def test_non_json_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout="Business Plan (human text)")
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))

    @patch(f"{_MOD}.subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="auggie", timeout=15)
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))

    @patch(f"{_MOD}.subprocess.run")
    def test_missing_binary_returns_none(self, mock_run):
        mock_run.side_effect = FileNotFoundError("auggie not found")
        self.assertIsNone(get_auggie_subscription_type("/usr/bin/auggie", Path.home()))


class TestWhichNoCwd(unittest.TestCase):
    """The PATH resolver must never return a binary planted in the CWD."""

    @patch(f"{_MOD}.shutil.which", return_value=None)
    def test_none_when_not_found(self, _w):
        self.assertIsNone(_which_no_cwd("auggie"))

    @patch(f"{_MOD}.shutil.which")
    def test_rejects_cwd_planted(self, mock_which):
        mock_which.return_value = os.path.join(os.getcwd(), "auggie")
        self.assertIsNone(_which_no_cwd("auggie"))

    @patch(f"{_MOD}.shutil.which", return_value="/usr/bin/auggie")
    def test_accepts_outside_cwd(self, _w):
        self.assertEqual(_which_no_cwd("auggie"), os.path.realpath("/usr/bin/auggie"))


if __name__ == "__main__":
    unittest.main()
