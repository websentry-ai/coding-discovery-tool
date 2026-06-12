"""Residue-vs-real detection tests for Claude Cowork (FIX 3).

On Linux and Windows the detector used to report Cowork whenever the on-disk
session tree (``<config>/Claude/local-agent-mode-sessions/``) existed. But the
per-user Claude config tree survives an uninstall (anthropics/claude-code#25013),
so the sessions dir alone is residue and produced false positives. Detection now
AND-requires a present Claude Desktop install (resolved by the OS detector's
``_find_install_dir``). macOS already AND-required ``/Applications/Claude.app``
and is unchanged.

Both routing entry points are covered:

* the central ``_detect_claude_cowork`` (``user_tool_detector.py``) — the
  production root/MDM path, which builds ``sessions_dir`` itself and delegates
  the install check to ``detector._find_install_dir``; and
* the OS ``detect()`` modules (Windows / Linux).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.claude_cowork_skills_helpers import COWORK_SESSIONS_DIR
from scripts.coding_discovery_tools.user_tool_detector import _detect_claude_cowork

_MOD = "scripts.coding_discovery_tools.user_tool_detector"


def _make_detector(install_dir=None):
    det = Mock()
    det.tool_name = "Claude Cowork"
    det.get_version.return_value = None
    det._find_install_dir = Mock(return_value=install_dir)
    return det


class TestCentralCoworkLinux(unittest.TestCase):
    """Central ``_detect_claude_cowork`` — Linux branch (root/MDM path)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_sessions(self):
        sdir = self.home / ".config" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_residue_sessions_only_not_detected(self):
        """Sessions tree present but NO install dir -> not detected (FP fix)."""
        self._make_sessions()
        det = _make_detector(install_dir=None)
        with patch(f"{_MOD}.platform.system", return_value="Linux"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)

    def test_sessions_plus_install_detected(self):
        sdir = self._make_sessions()
        det = _make_detector(install_dir=Path("/opt/Claude"))
        with patch(f"{_MOD}.platform.system", return_value="Linux"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Claude Cowork")
        self.assertEqual(result["install_path"], str(sdir))

    def test_no_sessions_not_detected_even_with_install(self):
        det = _make_detector(install_dir=Path("/opt/Claude"))
        with patch(f"{_MOD}.platform.system", return_value="Linux"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)
        # The install check is short-circuited (sessions absent first).
        det._find_install_dir.assert_not_called()

    def test_detector_missing_find_install_dir_not_detected(self):
        """Defensive: a detector without ``_find_install_dir`` (shouldn't happen
        on Linux/Windows) -> not detected rather than crashing."""
        self._make_sessions()
        det = Mock(spec=["tool_name", "get_version"])
        det.tool_name = "Claude Cowork"
        with patch(f"{_MOD}.platform.system", return_value="Linux"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)


class TestCentralCoworkWindows(unittest.TestCase):
    """Central ``_detect_claude_cowork`` — Windows branch."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_sessions(self):
        sdir = self.home / "AppData" / "Roaming" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_residue_sessions_only_not_detected(self):
        self._make_sessions()
        det = _make_detector(install_dir=None)
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)

    def test_sessions_plus_install_detected(self):
        sdir = self._make_sessions()
        det = _make_detector(install_dir=self.home / "AppData" / "Local" / "Programs" / "Claude")
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(sdir))


class TestCentralCoworkMacUnchanged(unittest.TestCase):
    """macOS branch is unchanged: it AND-gates ``/Applications/Claude.app`` and
    does NOT consult ``_find_install_dir``."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_sessions(self):
        sdir = self.home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_app_absent_not_detected(self):
        self._make_sessions()
        det = _make_detector(install_dir=None)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.Path.exists", return_value=False):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)

    def test_app_present_and_sessions_detected_without_find_install_dir(self):
        sdir = self._make_sessions()
        det = _make_detector(install_dir=None)
        real_exists = Path.exists

        def fake_exists(self):
            if str(self) == "/Applications/Claude.app":
                return True
            return real_exists(self)

        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch("pathlib.Path.exists", fake_exists):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(sdir))
        # macOS path never consults the install-dir delegate.
        det._find_install_dir.assert_not_called()


# ── OS detect() modules ──────────────────────────────────────────────────────

_WIN_MOD = "scripts.coding_discovery_tools.windows.claude_cowork.claude_cowork"
_LINUX_MOD = "scripts.coding_discovery_tools.linux.claude_cowork.claude_cowork"


class TestWindowsCoworkDetect(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.windows.claude_cowork.claude_cowork import (
            WindowsClaudeCoworkDetector,
        )
        self.Detector = WindowsClaudeCoworkDetector
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.appdata = self.home / "AppData" / "Roaming"

    def tearDown(self):
        self.tmp.cleanup()

    def _make_sessions(self):
        sdir = self.appdata / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_residue_sessions_only_not_detected(self):
        """Sessions present, no install dir on disk -> not detected (FP fix)."""
        self._make_sessions()
        with patch.dict(os.environ, {"APPDATA": str(self.appdata)}), \
             patch.object(self.Detector, "_find_install_dir", return_value=None):
            self.assertIsNone(self.Detector().detect())

    def test_sessions_plus_install_detected(self):
        self._make_sessions()
        install = self.home / "AppData" / "Local" / "Programs" / "Claude"
        with patch.dict(os.environ, {"APPDATA": str(self.appdata)}), \
             patch.object(self.Detector, "_find_install_dir", return_value=install):
            result = self.Detector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))

    def test_no_appdata_not_detected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.Detector().detect())


class TestLinuxCoworkDetect(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.linux.claude_cowork.claude_cowork import (
            LinuxClaudeCoworkDetector,
        )
        self.Detector = LinuxClaudeCoworkDetector
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_sessions(self):
        sdir = self.home / ".config" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_residue_sessions_only_not_detected(self):
        self._make_sessions()
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(self.Detector, "_find_install_dir", return_value=None):
            self.assertIsNone(self.Detector().detect())

    def test_sessions_plus_install_detected(self):
        self._make_sessions()
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(self.Detector, "_find_install_dir", return_value=Path("/opt/Claude")):
            result = self.Detector().detect()
        self.assertIsNotNone(result)
        # The OS module reports the resolved install dir as install_path.
        self.assertEqual(result["install_path"], "/opt/Claude")

    def test_multi_user_residue_does_not_leak(self):
        """Two users with sessions but NO install -> not detected for either."""
        self._make_sessions()
        home2 = Path(self.tmp.name + "_2")
        home2.mkdir()
        (home2 / ".config" / "Claude" / COWORK_SESSIONS_DIR).mkdir(parents=True)
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[self.home, home2]), \
             patch.object(self.Detector, "_find_install_dir", return_value=None):
            self.assertIsNone(self.Detector().detect())


if __name__ == "__main__":
    unittest.main()
