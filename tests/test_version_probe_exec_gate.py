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

    def test_gate_uses_the_real_ownership_rule(self):
        """No mocking: a world-writable dir is refused, a normal one is not."""
        self.assertEqual(run_command([str(self.binary), "--version"]), "1.2.3")
        os.chmod(self.dir, 0o777)
        self.assertIsNone(run_command([str(self.binary), "--version"]))


if __name__ == "__main__":
    unittest.main()
