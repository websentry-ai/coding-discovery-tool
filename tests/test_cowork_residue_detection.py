"""Residue-vs-real detection tests for Claude Cowork (FIX 3).

On Linux and Windows the detector used to report Cowork whenever the on-disk
session tree (``<config>/Claude/local-agent-mode-sessions/``) existed. But the
per-user Claude config tree survives an uninstall (anthropics/claude-code#25013),
so the sessions dir alone is residue and produced false positives. Detection now
AND-requires a present Claude Desktop install (resolved by the OS detector's
``_find_install_dir``). macOS resolves its bundle the same way, so a per-user
``~/Applications/Claude.app`` (a non-admin install on a managed Mac) is found too.

Both routing entry points are covered:

* the central ``_detect_claude_cowork`` (``user_tool_detector.py``) — the
  production root/MDM path, which builds ``sessions_dir`` itself and delegates
  the install check to ``detector._find_install_dir``; and
* the OS ``detect()`` modules (Windows / Linux).
"""

import os
import plistlib
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


class TestCentralCoworkMac(unittest.TestCase):
    """macOS resolves its install dir through ``_find_install_dir``, like Win/Linux,
    so a per-user ``~/Applications/Claude.app`` is not missed."""

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
        with patch(f"{_MOD}.platform.system", return_value="Darwin"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNone(result)

    @unittest.skipIf(os.name == "nt", "POSIX-only: macOS /Applications/Claude.app path semantics (backslash on Windows)")
    def test_system_app_detected_via_find_install_dir(self):
        sdir = self._make_sessions()
        det = _make_detector(install_dir=Path("/Applications/Claude.app"))
        with patch(f"{_MOD}.platform.system", return_value="Darwin"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(sdir))
        det._find_install_dir.assert_called_once_with(self.home)

    @unittest.skipIf(os.name == "nt", "POSIX-only: macOS ~/Applications path semantics")
    def test_per_user_applications_install_detected(self):
        sdir = self._make_sessions()
        det = _make_detector(install_dir=self.home / "Applications" / "Claude.app")
        with patch(f"{_MOD}.platform.system", return_value="Darwin"):
            result = _detect_claude_cowork(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(sdir))


# ── OS detect() modules ──────────────────────────────────────────────────────

_WIN_MOD = "scripts.coding_discovery_tools.windows.claude_cowork.claude_cowork"
_LINUX_MOD = "scripts.coding_discovery_tools.linux.claude_cowork.claude_cowork"
_MAC_MOD = "scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork"


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
        sdir = self._make_sessions()
        install = self.home / "AppData" / "Local" / "Programs" / "Claude"
        with patch.dict(os.environ, {"APPDATA": str(self.appdata)}), \
             patch.object(self.Detector, "_find_install_dir", return_value=install):
            result = self.Detector().detect()
        self.assertIsNotNone(result)
        # install_path is the gated SESSIONS dir (consistent with macOS + central path).
        self.assertEqual(result["install_path"], str(sdir))

    def test_no_appdata_not_detected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.Detector().detect())

    def test_central_scan_uses_scanned_user_home_not_scanner(self):
        """Regression (admin/MDM FN): the central path must resolve the install
        dir under the SCANNED user's home, not the scanner's Path.home(). User B
        has the per-user install; the scanner home has none -> B is still
        detected. (Real _find_install_dir, not mocked.)"""
        b_sessions = self.home / "AppData" / "Roaming" / "Claude" / COWORK_SESSIONS_DIR
        b_sessions.mkdir(parents=True)
        (self.home / "AppData" / "Local" / "Programs" / "Claude").mkdir(parents=True)
        scanner_home = Path(self.tmp.name + "_scanner")
        scanner_home.mkdir()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_WIN_MOD}.Path.home", return_value=scanner_home):
            result = _detect_claude_cowork(self.Detector(), self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(b_sessions))

    def test_central_scan_scanner_install_not_attributed_to_other_user(self):
        """Inverse: install only under the scanner's home, user B has only the
        sessions residue -> B not detected (no cross-user attribution)."""
        b_sessions = self.home / "AppData" / "Roaming" / "Claude" / COWORK_SESSIONS_DIR
        b_sessions.mkdir(parents=True)
        scanner_home = Path(self.tmp.name + "_scanner")
        (scanner_home / "AppData" / "Local" / "Programs" / "Claude").mkdir(parents=True)
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_WIN_MOD}.Path.home", return_value=scanner_home):
            result = _detect_claude_cowork(self.Detector(), self.home)
        self.assertIsNone(result)


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
        sdir = self._make_sessions()
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[self.home]), \
             patch.object(self.Detector, "_find_install_dir", return_value=Path("/opt/Claude")):
            result = self.Detector().detect()
        self.assertIsNotNone(result)
        # install_path is the gated SESSIONS dir (consistent with macOS + central path).
        self.assertEqual(result["install_path"], str(sdir))

    def test_multi_user_residue_does_not_leak(self):
        """Two users with sessions but NO install -> not detected for either."""
        self._make_sessions()
        home2 = Path(self.tmp.name + "_2")
        home2.mkdir()
        (home2 / ".config" / "Claude" / COWORK_SESSIONS_DIR).mkdir(parents=True)
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[self.home, home2]), \
             patch.object(self.Detector, "_find_install_dir", return_value=None):
            self.assertIsNone(self.Detector().detect())


@unittest.skipIf(os.name == "nt", "POSIX-only: macOS .app bundle path semantics")
class TestMacOSCoworkDetect(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        self.Detector = MacOSClaudeCoworkDetector
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_scanner = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.scanner_home = Path(self.tmp_scanner.name)
        # Neutralise any real /Applications/Claude.app on the machine running the suite.
        patcher = patch(f"{_MAC_MOD}.CLAUDE_DESKTOP_APP_PATH", self.home / "absent" / "Claude.app")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()
        self.tmp_scanner.cleanup()

    def _make_sessions(self, home=None):
        sdir = (home or self.home) / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def test_residue_sessions_only_not_detected(self):
        """Sessions present, no app bundle anywhere -> not detected (FP fix holds)."""
        self._make_sessions()
        with patch(f"{_MAC_MOD}.Path.home", return_value=self.home):
            self.assertIsNone(self.Detector().detect())

    def test_per_user_applications_install_detected(self):
        """Regression (FN): a non-admin ~/Applications install must be detected."""
        sdir = self._make_sessions()
        (self.home / "Applications" / "Claude.app").mkdir(parents=True)
        with patch(f"{_MAC_MOD}.Path.home", return_value=self.home):
            result = self.Detector().detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(sdir))

    def test_central_scan_uses_scanned_user_home_not_scanner(self):
        """Central path resolves the install under the SCANNED user's home."""
        b_sessions = self._make_sessions()
        (self.home / "Applications" / "Claude.app").mkdir(parents=True)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MAC_MOD}.Path.home", return_value=self.scanner_home):
            result = _detect_claude_cowork(self.Detector(), self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(b_sessions))

    def test_central_scan_scanner_install_not_attributed_to_other_user(self):
        """Inverse: the scanner's own ~/Applications install must not leak to user B."""
        self._make_sessions()
        (self.scanner_home / "Applications" / "Claude.app").mkdir(parents=True)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MAC_MOD}.Path.home", return_value=self.scanner_home):
            result = _detect_claude_cowork(self.Detector(), self.home)
        self.assertIsNone(result)

    def _write_bundle(self, home, version):
        plist = home / "Applications" / "Claude.app" / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        with plist.open("wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)

    def test_version_comes_from_scanned_users_bundle_not_scanner(self):
        """Version must be read from the scanned user's bundle, not the scanner's."""
        self._make_sessions()
        self._write_bundle(self.home, "ALICE-9.9.9")
        self._write_bundle(self.scanner_home, "SCANNER-0.0.1")
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MAC_MOD}.Path.home", return_value=self.scanner_home):
            result = _detect_claude_cowork(self.Detector(), self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "ALICE-9.9.9")

    def test_non_directory_bundle_not_detected(self):
        """A stale alias/file named Claude.app is residue, not an install."""
        self._make_sessions()
        app = self.home / "Applications" / "Claude.app"
        app.parent.mkdir(parents=True)
        app.write_text("stale finder alias")
        with patch(f"{_MAC_MOD}.Path.home", return_value=self.home):
            self.assertIsNone(self.Detector().detect())


if __name__ == "__main__":
    unittest.main()
