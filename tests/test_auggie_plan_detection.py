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

    def test_rejects_non_dns_label_chars(self):
        # Percent-encoding / underscores aren't real host chars — reject them so
        # curl can't parse the host differently than we validated it.
        self.assertIsNone(_augment_tenant_host("https://a%2f.augmentcode.com/"))
        self.assertIsNone(_augment_tenant_host("https://_.augmentcode.com/"))

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
        # These exercise read/parse mechanics on a tmpdir; on Windows the read is
        # gated to the scanning user's own home, so treat the tmpdir as a self-scan.
        own = patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
        self.addCleanup(own.stop)
        own.start()

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

    @unittest.skipIf(not hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_fifo_session_returns_none_without_blocking(self):
        # A low-priv user could plant a FIFO here; reading it must not block the
        # scanner (this test would hang if the open were blocking).
        aug = self.home / ".augment"
        aug.mkdir()
        os.mkfifo(str(aug / "session.json"))
        self.assertIsNone(_read_auggie_session(self.home))

    @unittest.skipIf(not hasattr(os, "symlink") or os.name == "nt", "POSIX symlink")
    def test_symlinked_session_refused(self):
        # session.json is a symlink (into another user's home in the real attack);
        # the redirect check refuses to open it.
        aug = self.home / ".augment"
        aug.mkdir()
        target = self.home / "real.json"
        target.write_text(
            json.dumps({"accessToken": _TOKEN, "tenantURL": _TENANT}), encoding="utf-8")
        os.symlink(str(target), str(aug / "session.json"))
        self.assertIsNone(_read_auggie_session(self.home))

    @unittest.skipIf(not hasattr(os, "symlink") or os.name == "nt", "POSIX symlink")
    def test_symlinked_augment_dir_refused(self):
        # ~/.augment itself is a symlink/junction into another user's dir — the
        # Windows-reparse-point vector. Refused even though the file inside is real
        # (the parent-dir redirect check, which a leaf-only O_NOFOLLOW would miss).
        victim = self.home / "victim_augment"
        victim.mkdir()
        (victim / "session.json").write_text(
            json.dumps({"accessToken": _TOKEN, "tenantURL": _TENANT}), encoding="utf-8")
        os.symlink(str(victim), str(self.home / ".augment"))
        self.assertIsNone(_read_auggie_session(self.home))

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX ownership")
    @patch(f"{_MOD}.platform.system", return_value="Linux")
    @patch(f"{_MOD}.os.fstat")
    def test_owner_mismatch_refused(self, mock_fstat, _sys):
        # POSIX: the opened fd's uid must match this home's owner, else refuse. Keep
        # the real inode/device (so the identity check passes) and change only the
        # uid, so the ownership barrier is what fires.
        _write_session(self.home)
        real = os.stat(str(self.home / ".augment" / "session.json"))
        mock_fstat.return_value = os.stat_result(
            (real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
             real.st_uid + 99999, real.st_gid, real.st_size,
             int(real.st_atime), int(real.st_mtime), int(real.st_ctime)))
        self.assertIsNone(_read_auggie_session(self.home))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=False)
    def test_windows_cross_user_read_refused(self, _own, _sys):
        # Windows has no cheap fd-owner check, so a cross-user (not-own-home) read
        # is refused outright — the junction swap-back can't be caught by pathname.
        _write_session(self.home)
        self.assertIsNone(_read_auggie_session(self.home))

    @patch(f"{_MOD}.platform.system", return_value="Windows")
    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
    def test_windows_own_home_read_allowed(self, _own, _sys):
        # Windows self-scan (our own home) reads normally.
        _write_session(self.home)
        self.assertEqual(_read_auggie_session(self.home), (_TENANT, _TOKEN))


class TestGetAuggieSubscriptionType(unittest.TestCase):
    """The billing HTTP call + parse, with a real session file on disk."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = _write_session(Path(self.tmp))
        self.curl = "/usr/bin/curl"
        p = patch(f"{_MOD}._trusted_curl", return_value=self.curl)
        self.addCleanup(p.stop)
        p.start()
        # On Windows the session read is gated to the scanning user's own home;
        # treat the tmpdir as a self-scan so these API/parse tests run there too.
        own = patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
        self.addCleanup(own.stop)
        own.start()
        # Isolate the billing-API path: keep the CLI fallback inert (no binary), so
        # a self-scan tmpdir doesn't invoke the real auggie on the test machine.
        w = patch(f"{_MOD}._which_no_cwd", return_value=None)
        self.addCleanup(w.stop)
        w.start()

    def test_none_home_returns_none(self):
        self.assertIsNone(get_auggie_subscription_type(None))

    @patch(f"{_MOD}._trusted_curl", return_value=None)
    @patch(f"{_MOD}.subprocess.run")
    def test_no_trusted_curl_returns_none(self, mock_run, _tc):
        # No system curl found -> soft-fail, and never invoke a PATH-resolved one.
        self.assertIsNone(get_auggie_subscription_type(self.home))
        mock_run.assert_not_called()

    def test_no_session_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(get_auggie_subscription_type(Path(empty)))

    @patch(f"{_MOD}.subprocess.run")
    def test_parses_plan_name(self, mock_run):
        mock_run.return_value = _proc(stdout=_BILLING_JSON)
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")

    @unittest.skipUnless(hasattr(os, "geteuid"), "cross-user read is POSIX-only")
    @patch(f"{_MOD}.subprocess.run")
    def test_reads_any_users_home_cross_user(self, mock_run):
        # POSIX: a home that is NOT the scanning user's still resolves, because it's
        # a file read (fd-owner verified) + HTTP call, not an execution. On Windows
        # this is refused (no cheap fd-owner check) — covered separately.
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
        # curl is pinned to an absolute trusted path (not bare "curl" via PATH).
        self.assertEqual(argv, [self.curl, "-q", "--config", "-"])
        self.assertTrue(os.path.isabs(argv[0]))
        self.assertNotIn(_TOKEN, " ".join(argv))
        cfg = mock_run.call_args.kwargs["input"]
        self.assertIn(_TOKEN, cfg)
        self.assertIn("get-billing-summary", cfg)
        self.assertIn('proto = "=https"', cfg)
        self.assertIn("max-filesize", cfg)

    @patch(f"{_MOD}.subprocess.run")
    def test_plan_name_is_stripped(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"plan_name": "  Business Plan  "}))
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")

    @patch(f"{_MOD}.subprocess.run")
    def test_oversized_plan_rejected(self, mock_run):
        mock_run.return_value = _proc(stdout=json.dumps({"plan_name": "x" * 101}))
        self.assertIsNone(get_auggie_subscription_type(self.home))

    @patch(f"{_MOD}.subprocess.run")
    def test_nonprintable_plan_rejected(self, mock_run):
        # Control char, DEL, and Unicode line/paragraph separators all rejected.
        for bad in ("Bus\x00iness", "Plan\x7f", "A" + chr(0x2028) + "B", "A" + chr(0x2029) + "B"):
            mock_run.return_value = _proc(stdout=json.dumps({"plan_name": bad}))
            self.assertIsNone(get_auggie_subscription_type(self.home), bad)

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

    @patch(f"{_MOD}.os.path.normcase", side_effect=str.lower)
    @patch(f"{_MOD}.os.getcwd", return_value="/Proj")
    @patch(f"{_MOD}.shutil.which", return_value="/proj/auggie")
    def test_rejects_cwd_planted_case_insensitive(self, _w, _cwd, _nc):
        # On Windows getcwd()/which casing can differ (C:\Proj vs c:\proj); folding
        # case (here simulated with str.lower) must still catch the plant.
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

    @patch(f"{_MOD}.os.path.commonpath", side_effect=ValueError("different drives"))
    @patch(f"{_MOD}.shutil.which", return_value="/usr/bin/auggie")
    def test_accepts_cross_drive_binary(self, _w, _cp):
        # On Windows a binary on another drive than cwd makes commonpath raise;
        # that means "not under cwd", so it must resolve, not be rejected.
        self.assertEqual(_which_no_cwd("auggie"), os.path.abspath("/usr/bin/auggie"))

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


class TestReadOwnRegularFileIdentity(unittest.TestCase):
    """The opened fd must be the exact file lstat saw, not a redirect swapped in
    around the open (the Windows-junction case, where O_NOFOLLOW can't help)."""

    def test_fd_identity_mismatch_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".augment").mkdir()
            target = home / ".augment" / "session.json"
            target.write_text("{}", encoding="utf-8")
            real = os.lstat(str(target))
            # Simulate the open landing on a DIFFERENT file than lstat saw (as a
            # junction/symlink swap would): fstat returns a mismatched inode/dev.
            seq = list(real)
            seq[1] = real.st_ino + 1  # st_ino
            seq[2] = real.st_dev + 1  # st_dev
            with patch(f"{_MOD}.os.fstat", return_value=os.stat_result(seq)):
                self.assertIsNone(utils._read_own_regular_file(target, home, 1000))

    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
    def test_matching_identity_reads_normally(self, _own):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".augment").mkdir()
            target = home / ".augment" / "session.json"
            target.write_text("hello", encoding="utf-8")
            self.assertEqual(utils._read_own_regular_file(target, home, 1000), "hello")


class TestAuggieCliFallback(unittest.TestCase):
    """When the billing API can't answer and we're scanning our OWN home, fall
    back to the user's auggie CLI; never run a binary in a cross-user scan."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = _write_session(Path(self.tmp))
        p = patch(f"{_MOD}._trusted_curl", return_value="/usr/bin/curl")
        self.addCleanup(p.stop)
        p.start()

    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
    @patch(f"{_MOD}._which_no_cwd", return_value="/opt/auggie")
    @patch(f"{_MOD}.subprocess.run")
    def test_falls_back_to_cli_on_token_failure(self, mock_run, _which, _own):
        # Dead/expired token -> curl non-zero -> self-scan CLI reports planName.
        mock_run.side_effect = [
            _proc(returncode=22),  # curl billing call fails
            _proc(stdout=json.dumps({"planName": "Business Plan"})),  # auggie CLI
        ]
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")
        cli_argv = mock_run.call_args_list[1].args[0]
        self.assertEqual(cli_argv, ["/opt/auggie", "account", "status", "--json"])

    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
    @patch(f"{_MOD}._which_no_cwd", return_value="/opt/auggie")
    @patch(f"{_MOD}.subprocess.run")
    def test_api_success_skips_cli(self, mock_run, _which, _own):
        mock_run.return_value = _proc(stdout=_BILLING_JSON)  # billing OK
        self.assertEqual(get_auggie_subscription_type(self.home), "Business Plan")
        self.assertEqual(mock_run.call_count, 1)  # only curl, no CLI
        _which.assert_not_called()

    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=False)
    @patch(f"{_MOD}._which_no_cwd")
    @patch(f"{_MOD}.subprocess.run")
    def test_no_cli_in_cross_user_scan(self, mock_run, _which, _own):
        mock_run.return_value = _proc(returncode=22)  # billing fails
        self.assertIsNone(get_auggie_subscription_type(self.home))
        _which.assert_not_called()  # binary never even resolved off self-scan

    @patch(f"{_MOD}._is_scanning_users_own_home", return_value=True)
    @patch(f"{_MOD}._which_no_cwd", return_value="/opt/auggie")
    @patch(f"{_MOD}.subprocess.run")
    def test_cli_plan_is_bounded(self, mock_run, _which, _own):
        mock_run.side_effect = [
            _proc(returncode=22),
            _proc(stdout=json.dumps({"planName": "x" * 101})),  # oversized
        ]
        self.assertIsNone(get_auggie_subscription_type(self.home))


if __name__ == "__main__":
    unittest.main()
