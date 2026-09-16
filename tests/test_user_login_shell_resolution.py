"""Per-user PATH resolution for root scans.

The explicit candidate lists cannot cover every install prefix, and the ``which``
backstop resolves the SCANNER's PATH so it is skipped under root, leaving MDM scans
with no fallback. ``user_login_shell_tool_path`` asks the scanned user's own login
shell instead.

``sudo`` is never actually executed here: these pin the command that would be run,
the guards that keep it from running at all, and the validation of whatever it
returns. The risky properties are the ones that would stall, mis-attribute, or
silently drop a tool on a scan walking every home, so those are what is asserted.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.coding_discovery_tools.utils import (
    _MARKER,
    _login_shell_cache,
    _safe_helper_cache,
    user_login_shell_tool_path,
)

_MOD = "scripts.coding_discovery_tools.utils"


@unittest.skipIf(os.name == "nt", "resolution is POSIX-only; there is no os.geteuid to patch")
class TestUserLoginShellResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        _login_shell_cache.clear()
        self.addCleanup(_login_shell_cache.clear)
        _safe_helper_cache.clear()
        _safe_helper_cache["sudo"] = "/fake/sudo"
        self.addCleanup(_safe_helper_cache.clear)
        self.binary = self.home / "custom-prefix" / "bin" / "claude"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("#!/bin/sh\n")
        self.binary.chmod(0o755)
        self.passwd = SimpleNamespace(pw_name="alice", pw_uid=501, pw_dir=str(self.home))

    def _run(self, stdout, returncode=0, tool="claude", **kw):
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run", return_value=completed, **kw) as run:
            fake_pwd.getpwuid.return_value = self.passwd
            result = user_login_shell_tool_path(tool, self.home)
        return result, run

    def _line(self, tool, path):
        return f"{_MARKER}{tool}\t{path}\n"

    def test_resolves_a_prefix_no_candidate_list_covers(self):
        result, _ = self._run(self._line("claude", self.binary))
        self.assertEqual(result, str(self.binary))

    def test_drops_privileges_and_cannot_hang(self):
        _, run = self._run(self._line("claude", self.binary))
        args, kwargs = run.call_args
        self.assertEqual(args[0][:6], ["/fake/sudo", "-n", "-u", "alice", "-i", "sh"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertGreater(kwargs["timeout"], 0)

    def test_a_profile_banner_does_not_hide_the_path(self):
        noisy = "Welcome to the corporate shell\nMOTD: patch Tuesday\n" + self._line("claude", self.binary)
        result, _ = self._run(noisy)
        self.assertEqual(result, str(self.binary))

    def test_one_invocation_serves_every_tool(self):
        other = self.binary.with_name("cursor-agent")
        other.write_text("#!/bin/sh\n")
        other.chmod(0o755)
        stdout = self._line("claude", self.binary) + self._line("cursor-agent", other)
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run", return_value=completed) as run:
            fake_pwd.getpwuid.return_value = self.passwd
            first = user_login_shell_tool_path("claude", self.home)
            second = user_login_shell_tool_path("cursor-agent", self.home)
        self.assertEqual((first, second), (str(self.binary), str(other)))
        self.assertEqual(run.call_count, 1)

    def test_home_owned_by_another_account_is_refused(self):
        self.passwd.pw_dir = "/var/root"
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run") as run:
            fake_pwd.getpwuid.return_value = self.passwd
            self.assertIsNone(user_login_shell_tool_path("claude", self.home))
        run.assert_not_called()

    def test_non_root_scan_does_not_shell_out(self):
        with patch(f"{_MOD}.os.geteuid", return_value=501), \
                patch(f"{_MOD}.subprocess.run") as run:
            self.assertIsNone(user_login_shell_tool_path("claude", self.home))
        run.assert_not_called()

    def test_a_blocking_profile_is_not_fatal(self):
        result, _ = self._run("", side_effect=subprocess.TimeoutExpired("sudo", 10))
        self.assertIsNone(result)

    def test_path_that_does_not_exist_resolves_to_none(self):
        result, _ = self._run(self._line("claude", self.home / "gone"))
        self.assertIsNone(result)

    def test_alias_output_resolves_to_none(self):
        result, _ = self._run(self._line("claude", "claude: aliased to claude --verbose"))
        self.assertIsNone(result)

    def test_unusable_answers_resolve_to_none(self):
        cases = {
            "missing": self.home / "gone",
            "alias": "claude: aliased to claude --verbose",
            "relative": "bin/claude",
        }
        for label, path in cases.items():
            with self.subTest(case=label):
                result, _ = self._run(self._line("claude", path))
                self.assertIsNone(result)

    @unittest.skipIf(os.name == "nt", "no execute bit to clear")
    def test_non_executable_answer_resolves_to_none(self):
        self.binary.chmod(0o644)
        result, _ = self._run(self._line("claude", self.binary))
        self.assertIsNone(result)

    def test_no_safe_sudo_skips_the_lookup(self):
        """Better to lose the fallback than to let root's PATH pick the helper."""
        _safe_helper_cache["sudo"] = None
        result, run = self._run(self._line("claude", self.binary))
        self.assertIsNone(result)
        run.assert_not_called()

    def test_another_accounts_binary_is_not_attributed(self):
        """A shared prefix on the PATH must not hand Bob's install to Alice."""
        with patch(f"{_MOD}.machine_global_binary_owned_by_user", return_value=False) as owned:
            result, _ = self._run(self._line("claude", self.binary))
        self.assertIsNone(result)
        owned.assert_called()

    def test_ownership_is_judged_on_the_symlink_target(self):
        link = self.home / "link" / "claude"
        link.parent.mkdir()
        link.symlink_to(self.binary)
        with patch(f"{_MOD}.machine_global_binary_owned_by_user", return_value=True) as owned:
            self._run(self._line("claude", link))
        self.assertEqual(Path(owned.call_args[0][0]), Path(os.path.realpath(self.binary)))

    def test_missing_passwd_entry_resolves_to_none(self):
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run") as run:
            fake_pwd.getpwuid.side_effect = KeyError("no such uid")
            self.assertIsNone(user_login_shell_tool_path("claude", self.home))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
