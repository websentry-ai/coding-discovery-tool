"""Per-user PATH resolution for root scans.

The explicit candidate lists cannot cover every install prefix, and the ``which``
backstop resolves the SCANNER's PATH so it is skipped under root, leaving MDM scans
with no fallback. ``user_login_shell_tool_path`` asks the scanned user's own login
shell instead.

``sudo`` is never actually executed here: these pin the command that would be run,
the guards that keep it from running at all, and the validation of whatever it
returns. The risky properties are the ones that would stall or mis-attribute a scan
walking every home on a machine, so those are what is asserted.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.coding_discovery_tools.utils import user_login_shell_tool_path

_MOD = "scripts.coding_discovery_tools.utils"


class TestUserLoginShellResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.binary = self.home / "custom-prefix" / "bin" / "claude"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("#!/bin/sh\n")
        self.binary.chmod(0o755)
        self.passwd = SimpleNamespace(pw_name="alice", pw_uid=501, pw_dir=str(self.home))

    def _run(self, stdout, returncode=0, **kw):
        """Patch root + passwd, and capture the subprocess call that would be made."""
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run", return_value=completed, **kw) as run:
            fake_pwd.getpwuid.return_value = self.passwd
            result = user_login_shell_tool_path("claude", self.home)
        return result, run

    def test_resolves_a_prefix_no_candidate_list_covers(self):
        result, _ = self._run(f"{self.binary}\n")
        self.assertEqual(result, str(self.binary))

    def test_drops_privileges_and_cannot_hang(self):
        _, run = self._run(f"{self.binary}\n")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["sudo", "-n", "-u", "alice", "-i", "command", "-v", "claude"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertGreater(kwargs["timeout"], 0)

    def test_non_root_scan_does_not_shell_out(self):
        with patch(f"{_MOD}.os.geteuid", return_value=501), \
                patch(f"{_MOD}.subprocess.run") as run:
            self.assertIsNone(user_login_shell_tool_path("claude", self.home))
        run.assert_not_called()

    def test_a_blocking_profile_is_not_fatal(self):
        _, run = self._run("", side_effect=subprocess.TimeoutExpired("sudo", 10))
        self.assertIsNone(_)

    def test_non_zero_exit_resolves_to_none(self):
        result, _ = self._run("", returncode=1)
        self.assertIsNone(result)

    def test_path_that_does_not_exist_resolves_to_none(self):
        result, _ = self._run(f"{self.home}/gone/claude\n")
        self.assertIsNone(result)

    def test_non_executable_result_resolves_to_none(self):
        self.binary.chmod(0o644)
        result, _ = self._run(f"{self.binary}\n")
        self.assertIsNone(result)

    def test_shell_builtin_answer_resolves_to_none(self):
        result, _ = self._run("claude: aliased to claude --verbose\n")
        self.assertIsNone(result)

    def test_missing_passwd_entry_resolves_to_none(self):
        with patch(f"{_MOD}.os.geteuid", return_value=0), \
                patch(f"{_MOD}.pwd") as fake_pwd, \
                patch(f"{_MOD}.subprocess.run") as run:
            fake_pwd.getpwuid.side_effect = KeyError("no such uid")
            self.assertIsNone(user_login_shell_tool_path("claude", self.home))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
