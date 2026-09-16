"""run_command refuses to execute a binary another account could have planted.

Discovery resolves tool binaries in user-writable prefixes (``~/.local/bin/claude``
is in the candidate list) and then runs them to read a version. Under an MDM scan
that runs as root, so a user who writes to their own home gets root execution.

``_is_safe_exec_path`` already encodes the rule. These pin that ``run_command``
consults it for an absolute argv[0], that a bare name is untouched so PATH lookups
and ``sudo``/``npm`` calls still work, and that refusing degrades to a missing
version rather than a missing tool.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.utils import run_command

_MOD = "scripts.coding_discovery_tools.utils"


class TestVersionProbeExecGate(unittest.TestCase):
    def setUp(self):
        root = patch(f"{_MOD}._running_as_root", return_value=True)
        root.start()
        self.addCleanup(root.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.binary = self.dir / "claude"
        self.binary.write_text("#!/bin/sh\necho 1.2.3\n")
        self.binary.chmod(0o755)

    def test_unsafe_absolute_binary_is_not_executed(self):
        with patch(f"{_MOD}._is_safe_exec_path", return_value=False), \
                patch(f"{_MOD}.subprocess.run") as run:
            self.assertIsNone(run_command([str(self.binary), "--version"]))
        run.assert_not_called()

    @unittest.skipIf(os.name == "nt", "the shim is a POSIX shell script")
    def test_safe_absolute_binary_still_runs(self):
        with patch(f"{_MOD}._is_safe_exec_path", return_value=True):
            self.assertEqual(run_command([str(self.binary), "--version"]), "1.2.3")

    def test_bare_name_is_never_gated(self):
        with patch(f"{_MOD}._is_safe_exec_path", return_value=False) as gate:
            run_command(["echo", "hello"])
        gate.assert_not_called()

    def test_sudo_and_which_style_calls_are_unaffected(self):
        with patch(f"{_MOD}._is_safe_exec_path", return_value=False) as gate:
            self.assertEqual(run_command(["echo", "ok"]), "ok")
        gate.assert_not_called()

    def test_empty_command_does_not_raise(self):
        self.assertIsNone(run_command([]))

    @unittest.skipIf(os.name == "nt", "_is_safe_exec_path is a no-op on Windows")
    def test_gate_uses_the_real_ownership_rule(self):
        """No mocking: a world-writable dir is refused, a normal one is not."""
        self.assertEqual(run_command([str(self.binary), "--version"]), "1.2.3")
        os.chmod(self.dir, 0o777)
        self.assertIsNone(run_command([str(self.binary), "--version"]))

    @unittest.skipIf(os.name == "nt", "_is_safe_exec_path is a no-op on Windows")
    def test_a_writable_ancestor_is_refused(self):
        nested = self.dir / "a" / "b"
        nested.mkdir(parents=True)
        binary = nested / "claude"
        binary.write_text("#!/bin/sh\necho 1.2.3\n")
        binary.chmod(0o755)
        self.assertEqual(run_command([str(binary), "--version"]), "1.2.3")
        os.chmod(self.dir / "a", 0o777)
        self.assertIsNone(run_command([str(binary), "--version"]))

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_executes_the_path_it_validated(self):
        link = self.dir / "sub" / "claude"
        link.parent.mkdir()
        link.symlink_to(self.binary)
        with patch(f"{_MOD}.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="1.2.3", stderr="")
            run_command([str(link), "--version"])
        self.assertEqual(run.call_args[0][0][0], os.path.realpath(self.binary))

    @unittest.skipIf(os.name == "nt", "_is_safe_exec_path is a no-op on Windows")
    def test_symlink_into_an_unsafe_target_is_refused(self):
        unsafe_dir = self.dir / "unsafe"
        unsafe_dir.mkdir()
        target = unsafe_dir / "claude"
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
        os.chmod(unsafe_dir, 0o777)
        link = self.dir / "looks-fine"
        link.symlink_to(target)
        self.assertIsNone(run_command([str(link), "--version"]))

    def test_non_root_scan_is_not_gated_at_all(self):
        with patch(f"{_MOD}._running_as_root", return_value=False), \
                patch(f"{_MOD}._is_safe_exec_path") as gate, \
                patch(f"{_MOD}.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="1.2.3", stderr="")
            self.assertEqual(run_command([str(self.binary), "--version"]), "1.2.3")
        gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
