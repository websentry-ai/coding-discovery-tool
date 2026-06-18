"""WEB-4771: the Linux Cursor IDE detector must not report a phantom Cursor from
``~/.cursor`` residue.

``~/.cursor`` survives uninstall and is shared with Cursor CLI / rules tooling,
so a bare-dir-exists fallback reported Cursor after the IDE was gone. The
detector now gates on the real binary only (PATH / system / per-user binary
paths), matching the macOS (``Cursor.app``) and Windows (``.exe``) detectors.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.linux.cursor import cursor as mod


class TestLinuxCursorResidue(unittest.TestCase):
    def setUp(self):
        self.detector = mod.LinuxCursorDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dot_cursor_residue_only_not_detected(self):
        """``~/.cursor`` present, no binary anywhere -> NOT detected (FP fix)."""
        (self.home / ".cursor").mkdir()
        with patch.object(mod, "run_command", return_value=None), \
             patch.object(mod, "get_linux_user_homes", return_value=[self.home]), \
             patch.object(mod, "_SYSTEM_PATHS", []):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_real_user_binary_detected(self):
        """A real ``~/.local/bin/cursor`` binary -> still detected (even with the
        ``~/.cursor`` residue dir also present)."""
        (self.home / ".cursor").mkdir()
        binary = self.home / ".local" / "bin" / "cursor"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
        with patch.object(mod, "run_command", return_value=None), \
             patch.object(mod, "get_linux_user_homes", return_value=[self.home]), \
             patch.object(mod, "_SYSTEM_PATHS", []):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_which_on_path_detected(self):
        """``which cursor`` resolving -> detected (a real PATH install)."""
        with patch.object(mod, "run_command", return_value="/usr/bin/cursor\n"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/usr/bin/cursor")


if __name__ == "__main__":
    unittest.main()
