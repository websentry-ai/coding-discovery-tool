"""Tests for Auggie CLI (Augment) subscription plan detection.

Auggie stores no plan on disk, so the plan is read by taking the user's session
token from ``~/.augment/session.json`` and querying Augment's billing endpoint
with curl — a file read plus an HTTP call, never executing the user's binary, so
it works for every user in a privileged all-users scan. The session read and the
tenant-URL validation (SSRF guard) are tested separately from the HTTP/parse
logic. ``_which_no_cwd`` / ``_is_scanning_users_own_home`` remain in use by the
detectors (for install_path/version) and are covered here too.
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
    _read_auggie_session,
    _augment_tenant_host,
    _which_no_cwd,
    _is_scanning_users_own_home,
)

_MOD = "scripts.coding_discovery_tools.utils"
_TOKEN = "a" * 64
_TENANT = "https://d19.api.augmentcode.com/"
_BILLING_JSON = json.dumps({"plan_name": "Business Plan", "usage_unit": 2})


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _write_session(home, token=_TOKEN, tenant=_TENANT):
    aug = Path(home) / ".augment"
    aug.mkdir(parents=True, exist_ok=True)
    (aug / "session.json").write_text(
        json.dumps({"accessToken": token, "tenantURL": tenant, "scopes": ["email"]}),
        encoding="utf-8")
    return Path(home)


class TestAugmentTenantHost(unittest.TestCase):
    """SSRF / config-injection guard: the token may only go to an Augment tenant."""

    def test_accepts_augment_tenant(self):
        self.assertEqual(_augment_tenant_host("https://d19.api.augmentcode.com/"),
                         "d19.api.augmentcode.com")
        self.assertEqual(_augment_tenant_host("https://augmentcode.com"),
                         "augmentcode.com")

    def test_rejects_non_https(self):
        self.assertIsNone(_augment_tenant_host("http://d19.api.augmentcode.com/"))

    def test_rejects_suffix_lookalike(self):
        self.assertIsNone(_augment_tenant_host("https://augmentcode.com.evil.com/"))

    def test_rejects_userinfo_trick(self):
        self.assertIsNone(_augment_tenant_host("https://augmentcode.com@evil.com/"))

    def test_rejects_unrelated_host(self):
        self.assertIsNone(_augment_tenant_host("https://evil.com/"))

    def test_rejects_ip_and_ipv6(self):
        self.assertIsNone(_augment_tenant_host("https://127.0.0.1/"))
        self.assertIsNone(_augment_tenant_host("https://[::1]/"))

    def test_rejects_whitespace_or_control_chars(self):
        # A newline in the URL could inject a line into the curl config.
        self.assertIsNone(_augment_tenant_host("https://augmentcode.com/\nfoo"))
        self.assertIsNone(_augment_tenant_host("https://augmentcode.com/ x"))

    def test_rejects_garbage(self):
        self.assertIsNone(_augment_tenant_host("not a url"))
        self.assertIsNone(_augment_tenant_host(""))


class TestReadAuggieSession(unittest.TestCase):
    """Reading + validating ~/.augment/session.json (works cross-user as a file)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = Path(self.tmp)

    def test_valid_session_returns_base_and_token(self):
        _write_session(self.home)
        got = _read_auggie_session(self.home)
        self.assertEqual(got, (_TENANT, _TOKEN))

    def test_tenant_url_gets_trailing_slash(self):
        _write_session(self.home, tenant="https://d19.api.augmentcode.com")
        base, _ = _read_auggie_session(self.home)
        self.assertEqual(base, "https://d19.api.augmentcode.com/")

    def test_base_url_rebuilt_from_host_discards_path(self):
        # The session's own path/query is untrusted and thrown away; the base is
        # rebuilt as https://<host>/ so no curl directive can be smuggled in.
        _write_session(self.home, tenant="https://d19.api.augmentcode.com/x/y?q=1")
        base, _ = _read_auggie_session(self.home)
        self.assertEqual(base, "https://d19.api.augmentcode.com/")

    def test_newline_in_tenant_url_returns_none(self):
        _write_session(self.home, tenant="https://d19.api.augmentcode.com/\nheader = evil")
        self.assertIsNone(_read_auggie_session(self.home))

    def test_missing_file_returns_none(self):
        self.assertIsNone(_read_auggie_session(self.home))

    def test_bad_json_returns_none(self):
        aug = self.home / ".augment"
        aug.mkdir()
        (aug / "session.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(_read_auggie_session(self.home))

    def test_missing_token_returns_none(self):
        aug = self.home / ".augment"
        aug.mkdir()
        (aug / "session.json").write_text(json.dumps({"tenantURL": _TENANT}), encoding="utf-8")
        self.assertIsNone(_read_auggie_session(self.home))

    def test_control_char_token_returns_none(self):
        _write_session(self.home, token="abc\ndef")
        self.assertIsNone(_read_auggie_session(self.home))

    def test_non_augment_tenant_returns_none(self):
        # Even with a valid-looking token, an off-domain tenant is refused so the
        # token is never sent off Augment's servers.
        _write_session(self.home, tenant="https://evil.com/")
        self.assertIsNone(_read_auggie_session(self.home))


class TestGetAuggieSubscriptionType(unittest.TestCase):
    """The billing HTTP call + parse, with a real session file on disk."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = _write_session(Path(self.tmp))

    def test_none_home_returns_none(self):
        self.assertIsNone(get_auggie_subscription_type(None))

    def test_no_session_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(get_auggie_subscription_type(Path(empty)))

    @patch(f"{_MOD}.subprocess.run")
    def test_parses_plan_name(self, mock_run):
        mock_run.return_value = _proc(stdout=_BILLING_JSON)
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")

    @patch(f"{_MOD}.subprocess.run")
    def test_reads_any_users_home_cross_user(self, mock_run):
        # The whole point: a home that is NOT the scanning user's still resolves,
        # because it's a file read + HTTP call, not an execution.
        import tempfile
        with tempfile.TemporaryDirectory() as other:
            _write_session(Path(other), token="b" * 64)
            mock_run.return_value = _proc(stdout=_BILLING_JSON)
            self.assertEqual(get_auggie_subscription_type(Path(other)), "Business Plan")

    @patch(f"{_MOD}.subprocess.run")
    def test_token_passed_via_stdin_not_argv(self, mock_run):
        # The bearer token must never land in the process argv (ps-visible on a
        # shared machine); it goes through the curl config on stdin.
        mock_run.return_value = _proc(stdout=_BILLING_JSON)
        get_auggie_subscription_type(self.home)
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv, ["curl", "--config", "-"])
        self.assertNotIn(_TOKEN, " ".join(argv))
        cfg = mock_run.call_args.kwargs["input"]
        self.assertIn(_TOKEN, cfg)
        self.assertIn("get-billing-summary", cfg)

    @patch(f"{_MOD}.subprocess.run")
    def test_plan_name_is_stripped(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"plan_name": "  Business Plan  "}))
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")

    @patch(f"{_MOD}.subprocess.run")
    def test_oversized_plan_rejected(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"plan_name": "x" * 101}))
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_control_char_plan_rejected(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"plan_name": "Bus\x00iness"}))
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_missing_plan_name_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"usage_unit": 2}))
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_non_zero_exit_returns_none_without_logging_body(self, mock_run):
        mock_run.return_value = _proc(returncode=22, stdout="token=abc123 leak")
        with self.assertLogs(utils.logger, level="DEBUG") as cm:
            self.assertIsNone(get_auggie_subscription_type(self.home))
        self.assertFalse(any("abc123" in line for line in cm.output))

    @patch(f"{_MOD}.subprocess.run")
    def test_non_json_returns_none(self, mock_run):
        mock_run.return_value = _proc(stdout="Business Plan (human text)")
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="curl", timeout=15)
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_curl_missing_returns_none(self, mock_run):
        mock_run.side_effect = FileNotFoundError("curl not found")
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_off_domain_session_never_calls_curl(self, mock_run):
        # A tampered tenant URL is rejected at read time, so curl is never run.
        _write_session(self.home, tenant="https://evil.com/")
        self.assertIsNone(get_auggie_subscription_type(self.home))
        mock_run.assert_not_called()


class TestWhichNoCwd(unittest.TestCase):
    """The PATH resolver (used by the detectors) must never return a CWD plant."""

    @patch(f"{_MOD}.shutil.which", return_value=None)
    def test_none_when_not_found(self, _w):
        self.assertIsNone(_which_no_cwd("auggie"))

    @patch(f"{_MOD}.shutil.which")
    def test_rejects_cwd_planted(self, mock_which):
        mock_which.return_value = os.path.join(os.getcwd(), "auggie")
        self.assertIsNone(_which_no_cwd("auggie"))

    @patch(f"{_MOD}.os.getcwd", return_value=os.path.abspath("proj"))
    @patch(f"{_MOD}.shutil.which")
    def test_rejects_plant_nested_under_cwd(self, mock_which, _cwd):
        # A hit one level down (e.g. <cwd>/node_modules/.bin) is still planted.
        mock_which.return_value = os.path.join(
            os.path.abspath("proj"), "node_modules", ".bin", "auggie")
        self.assertIsNone(_which_no_cwd("auggie"))

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_rejects_plant_with_symlinked_parent_escaping_tree(self):
        # Attacker owns the working dir and points a nested dir out of the tree:
        # <cwd>/nm -> <outside>. The hit still lives under the writable cwd on
        # paper, so it must be refused even though realpath escapes the tree.
        import tempfile
        with tempfile.TemporaryDirectory() as cwd, \
                tempfile.TemporaryDirectory() as outside:
            planted = os.path.join(outside, "auggie")
            open(planted, "w").close()
            os.symlink(outside, os.path.join(cwd, "nm"))
            hit = os.path.join(cwd, "nm", "auggie")
            with patch(f"{_MOD}.os.getcwd", return_value=cwd), \
                    patch(f"{_MOD}.shutil.which", return_value=hit):
                self.assertIsNone(_which_no_cwd("auggie"))

    @patch(f"{_MOD}.shutil.which", return_value="/usr/bin/auggie")
    def test_accepts_outside_cwd(self, _w):
        self.assertEqual(_which_no_cwd("auggie"), os.path.abspath("/usr/bin/auggie"))

    @patch(f"{_MOD}.os.getcwd", return_value=os.sep)
    @patch(f"{_MOD}.shutil.which", return_value="/usr/bin/auggie")
    def test_accepts_when_cwd_is_ancestor(self, _w, _cwd):
        # cwd='/' (launchd/systemd default) is an ancestor of every path, but the
        # binary isn't *in* it — a real install must still resolve.
        self.assertEqual(_which_no_cwd("auggie"), os.path.abspath("/usr/bin/auggie"))

    @patch(f"{_MOD}.os.getcwd", return_value=os.sep)
    @patch(f"{_MOD}.shutil.which", return_value=os.sep + "auggie")
    def test_rejects_plant_directly_in_root_cwd(self, _w, _cwd):
        # The root exemption is only for "cwd is an ancestor"; a binary planted
        # directly in a writable root cwd (e.g. D:\auggie) is still refused.
        self.assertIsNone(_which_no_cwd("auggie"))


class TestIsScanningUsersOwnHome(unittest.TestCase):
    """The shared own-home gate used by both detectors' PATH fallback."""

    def test_none_home_refused(self):
        self.assertFalse(_is_scanning_users_own_home(None))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    def test_posix_own_passwd_home_allowed(self, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        self.assertTrue(_is_scanning_users_own_home(Path("/home/alice")))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    def test_posix_uses_passwd_not_spoofable_env_home(self, mock_pwd, _euid, _sys):
        # $HOME could be spoofed to another user's home under sudo -E; the gate
        # must compare against the passwd home, not Path.home().
        mock_pwd.getpwuid.return_value = MagicMock(pw_dir="/home/alice")
        self.assertFalse(_is_scanning_users_own_home(Path("/home/bob")))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=0, create=True)
    def test_posix_root_refused(self, _euid, _sys):
        self.assertFalse(_is_scanning_users_own_home(Path("/home/alice")))

    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.geteuid", return_value=1000, create=True)
    @patch(f"{_MOD}.pwd")
    def test_posix_missing_passwd_entry_refused(self, mock_pwd, _euid, _sys):
        mock_pwd.getpwuid.side_effect = KeyError("no such uid")
        self.assertFalse(_is_scanning_users_own_home(Path("/home/alice")))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._windows_process_is_elevated", return_value=True)
    def test_windows_elevated_refused(self, _elev, _sys):
        # Fail-closed: an elevated (or unknown-elevation) scan is refused, unlike
        # the detector's old fail-open is_running_as_admin().
        self.assertFalse(_is_scanning_users_own_home(Path.home()))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._windows_process_is_elevated", return_value=False)
    def test_windows_own_home_allowed(self, _elev, _sys):
        self.assertTrue(_is_scanning_users_own_home(Path.home()))


if __name__ == "__main__":
    unittest.main()
