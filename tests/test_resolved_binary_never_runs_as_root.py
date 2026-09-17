"""A user-owned binary reaches the inventory but never the CPU, under a root scan.

The helper tests assert what ``user_login_shell_tool_path`` returns. They cannot see
what a caller then does with it, which is how the auth probe stayed unguarded: it
calls ``subprocess.run`` itself rather than going through ``run_command``.

These assert at the consumer end instead. Every executor reachable from a resolved
binary path is driven with euid=0 and a user-owned target, and the assertion is that
nothing spawns.
"""

import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as U

_MOD = "scripts.coding_discovery_tools.utils"


@unittest.skipIf(os.name == "nt", "_is_safe_exec_path is a no-op on Windows")
@unittest.skipUnless(sys.platform == "darwin", "asserts macOS helper paths (launchctl)")
class TestResolvedBinaryNeverRunsAsRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.binary = self.home / "bin" / "claude"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("#!/bin/sh\necho PWNED\n")
        self.binary.chmod(0o755)

    def test_version_probe_does_not_spawn(self):
        with patch.object(U.os, "geteuid", return_value=0), \
                patch.object(U.subprocess, "run") as run:
            self.assertIsNone(U.run_command([str(self.binary), "--version"]))
        run.assert_not_called()

    def test_auth_probe_does_not_spawn(self):
        with patch.object(U.os, "geteuid", return_value=0), \
                patch.object(U.subprocess, "run") as run:
            ok, plan, method, key = U._run_auth_status(
                [str(self.binary), "auth", "status", "--json"], "alice", method="direct"
            )
        run.assert_not_called()
        self.assertEqual((ok, plan), (False, None))

    def test_symlink_to_another_binary_is_refused(self):
        link = self.home / "bin" / "claude-link"
        link.symlink_to("/usr/bin/uname")
        renamed = self.home / "bin" / "claude2"
        os.symlink("/usr/bin/uname", renamed)
        with patch.object(U.os, "geteuid", return_value=0):
            self.assertIsNone(U.safe_exec_argv([str(renamed), "--version"]))

    def test_npm_shim_naming_still_passes(self):
        """npm ships claude as claude.exe, so the stem must match, not the filename."""
        real = self.home / "lib" / "claude.exe"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/sh\n"); real.chmod(0o755)
        shim = self.home / "bin" / "claude-shim"
        shim.symlink_to(real)
        with patch.object(U.os, "geteuid", return_value=501):
            self.assertIsNotNone(U.safe_exec_argv([str(shim), "--version"]))

    def test_privilege_dropping_branches_are_untouched(self):
        """launchctl/su already run as the target user, so they must not be gated."""
        for cmd in (
            ["launchctl", "asuser", "501", "sudo", "-n", "-u", "alice", "/bin/zsh", "-lc", f"{self.binary} auth status"],
            ["su", "-", "alice", "-c", f"{self.binary} auth status"],
        ):
            with self.subTest(cmd=cmd[0]):
                self.assertEqual(U.safe_exec_argv(list(cmd)), cmd)

    def test_root_owned_binary_still_runs(self):
        self.assertIsNotNone(U.safe_exec_argv(["/usr/bin/uname", "-s"]))

    def test_profile_cannot_rename_another_binary_as_a_tool(self):
        entry = type("E", (), {"pw_name": "alice", "pw_uid": 501, "pw_dir": str(self.home)})()
        stdout = f"{U._MARKER}claude\t/usr/bin/uname\n"
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        U._login_shell_cache.clear()
        self.addCleanup(U._login_shell_cache.clear)
        with patch.object(U.os, "geteuid", return_value=0), \
                patch.object(U, "pwd") as fake_pwd, \
                patch.object(U.subprocess, "run", return_value=completed):
            fake_pwd.getpwuid.return_value = entry
            self.assertIsNone(U.user_login_shell_tool_path("claude", self.home))

    def _plan_probe_calls(self, helper):
        calls = []
        with patch.object(U.platform, "system", return_value="Darwin"), \
                patch.object(U, "_is_root", return_value=True), \
                patch.object(U.os, "geteuid", return_value=0), \
                patch.object(U, "_get_uid_for_user", return_value=501), \
                patch.object(U, "_get_compatible_shell", return_value="/bin/zsh"), \
                patch.object(U, "_safe_helper", side_effect=helper), \
                patch.object(U, "_is_safe_exec_path", return_value=True), \
                patch.object(U.subprocess, "run") as run:
            run.side_effect = lambda cmd, **kw: (
                calls.append(cmd),
                subprocess.CompletedProcess([], 1, stdout="", stderr=""),
            )[1]
            U.get_claude_subscription_type("alice", str(self.binary), user_home=self.home)
        return calls

    def test_root_execs_resolve_to_absolute_helpers(self):
        """launchctl and sudo run before any privilege drop, so PATH must not pick them."""
        calls = self._plan_probe_calls(lambda name: f"/sbin/{name}")
        probe = next(c for c in calls if len(c) > 3 and str(c[3]).endswith("sudo"))
        self.assertEqual([probe[0], probe[3]], ["/sbin/launchctl", "/sbin/sudo"])

    def test_an_unsafe_helper_skips_the_branch_rather_than_going_bare(self):
        calls = self._plan_probe_calls(lambda name: None)
        self.assertFalse([c for c in calls if "launchctl" in str(c[0])])

    def test_a_refusal_is_reported_as_its_own_cause(self):
        """Refusing must not read as a logged-out user, or the alert stays silent."""
        diagnostics = []
        with patch.object(U.platform, "system", return_value="Linux"), \
                patch.object(U, "_is_root", return_value=True), \
                patch.object(U.os, "geteuid", return_value=0):
            U.get_claude_subscription_type(
                "alice", str(self.binary), diagnostics=diagnostics, user_home=self.home
            )
        direct = [d for d in diagnostics if d["category"] == "direct_exec"]
        self.assertTrue(direct, "no direct_exec diagnostic was recorded")
        self.assertTrue(direct[0]["data"]["gate_refused"])


if __name__ == "__main__":
    unittest.main()
