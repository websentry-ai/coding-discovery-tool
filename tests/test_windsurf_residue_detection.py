"""WEB-4771: the Linux Windsurf detector must not report a phantom Windsurf from
``~/.windsurf`` residue.

``~/.windsurf`` (~475 MB) survives uninstall, so a bare-dir-exists fallback
reported Windsurf after the IDE was gone. The detector now gates on the real
binary only (PATH / system / per-user binary paths), matching the macOS
(``Windsurf.app``) and Windows (``.exe``) detectors.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.linux.windsurf import windsurf as mod


class TestLinuxWindsurfResidue(unittest.TestCase):
    def setUp(self):
        self.detector = mod.LinuxWindsurfDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dot_windsurf_residue_only_not_detected(self):
        """``~/.windsurf`` present, no binary anywhere -> NOT detected (FP fix)."""
        (self.home / ".windsurf").mkdir()
        with patch.object(mod, "run_command", return_value=None), \
             patch.object(mod, "get_linux_user_homes", return_value=[self.home]), \
             patch.object(mod, "_SYSTEM_PATHS", []):
            result = self.detector.detect()
        self.assertIsNone(result)

    def test_real_user_binary_detected(self):
        """A real ``~/.local/bin/windsurf`` binary -> still detected (even with the
        ``~/.windsurf`` residue dir also present)."""
        (self.home / ".windsurf").mkdir()
        binary = self.home / ".local" / "bin" / "windsurf"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
        with patch.object(mod, "run_command", return_value=None), \
             patch.object(mod, "get_linux_user_homes", return_value=[self.home]), \
             patch.object(mod, "_SYSTEM_PATHS", []):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_which_on_path_detected(self):
        """``which windsurf`` resolving -> detected (a real PATH install)."""
        with patch.object(mod, "run_command", return_value="/usr/bin/windsurf\n"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/usr/bin/windsurf")


if __name__ == "__main__":
    unittest.main()
