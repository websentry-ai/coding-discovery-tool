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


class TestCoworkProbeTelemetry(unittest.TestCase):
    """Both halves of the gate returned a bare None, so absent, denied and
    never-installed were one answer. The probe says which."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        utils_mod.reset_sentry_run_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Neutralise Spotlight, like the real-bundle patch the OS tests already use.
        spotlight = patch(f"{_MAC_MOD}.run_command", return_value=None)
        spotlight.start()
        self.addCleanup(spotlight.stop)

    def tearDown(self):
        self.tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def _sessions(self):
        sdir = self.home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
        sdir.mkdir(parents=True)
        return sdir

    def _detect(self, install_dir):
        with patch(f"{_MOD}.platform.system", return_value="Darwin"):
            return _detect_claude_cowork(_make_detector(install_dir), self.home)

    def test_dir_state_separates_denied_from_absent(self):
        self.assertEqual("absent", utils_mod.dir_state(self.home / "nope"))
        self.assertEqual("present", utils_mod.dir_state(self.home))

    def test_sessions_absent(self):
        self.assertIsNone(self._detect(None))
        self.assertIn("sessions:absent", utils_mod.cowork_probes())

    def test_sessions_present_bundle_absent(self):
        self._sessions()
        self.assertIsNone(self._detect(None))
        self.assertIn("sessions:present", utils_mod.cowork_probes())

    def test_both_present(self):
        self._sessions()
        self.assertIsNotNone(self._detect(Path("/Applications/Claude.app")))
        self.assertIn("sessions:present", utils_mod.cowork_probes())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX mode bits, and root ignores them")
    def test_denied_sessions_in_our_own_home_raises_so_nothing_is_pruned(self):
        """Unknown presence, not absence: only a raise marks the scan incomplete."""
        self._sessions()
        claude_dir = self.home / "Library" / "Application Support" / "Claude"
        os.chmod(claude_dir, 0o000)
        try:
            with patch(f"{_MOD}._is_scanning_users_own_home", return_value=True):
                with self.assertRaises(PermissionError):
                    self._detect(Path("/Applications/Claude.app"))
            self.assertIn("sessions:unreadable", utils_mod.cowork_probes())
        finally:
            os.chmod(claude_dir, 0o700)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX mode bits, and root ignores them")
    def test_denied_sessions_in_another_users_home_does_not_fail_the_scan(self):
        """macOS homes are 0700, so an unprivileged scan cannot read a sibling home.
        Raising there would mark every scan on every multi-user box incomplete and
        nothing would ever be pruned."""
        self._sessions()
        claude_dir = self.home / "Library" / "Application Support" / "Claude"
        os.chmod(claude_dir, 0o000)
        try:
            with patch(f"{_MOD}._is_scanning_users_own_home", return_value=False), \
                    patch(f"{_MOD}._is_root", return_value=False):
                self.assertIsNone(self._detect(Path("/Applications/Claude.app")))
            # Still recorded, so the fleet can see it even though the scan stays clean.
            self.assertIn("sessions:unreadable", utils_mod.cowork_probes())
        finally:
            os.chmod(claude_dir, 0o700)

    def _mac_detector(self):
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        return MacOSClaudeCoworkDetector()

    def test_find_install_dir_records_bundle_absent(self):
        det = self._mac_detector()
        with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[self.home / "nope.app"]):
            self.assertIsNone(det._find_install_dir(self.home))
        self.assertIn("bundle:absent", utils_mod.cowork_probes())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX mode bits, and root ignores them")
    def test_denied_bundle_raises_so_the_install_is_not_pruned(self):
        det = self._mac_detector()
        apps = self.home / "Applications"
        (apps / "Claude.app").mkdir(parents=True)
        os.chmod(apps, 0o000)
        try:
            with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[apps / "Claude.app"]):
                with self.assertRaises(PermissionError):
                    det._find_install_dir(self.home)
            self.assertIn("bundle:unreadable", utils_mod.cowork_probes())
        finally:
            os.chmod(apps, 0o700)


class TestCoworkSpotlightFallback(unittest.TestCase):
    """Two hard-coded paths miss an install anywhere else, and a Cowork user whose
    bundle we cannot find reports as having no tool at all."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        utils_mod.reset_sentry_run_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def _resolve(self, mdfind_output):
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            _spotlight_candidates,
        )
        with patch(f"{_MAC_MOD}.run_command", return_value=mdfind_output):
            return _spotlight_candidates(self.home)

    def test_accepts_machine_wide_and_the_scanned_users_own(self):
        self.assertEqual([Path("/Applications/Claude.app")], self._resolve("/Applications/Claude.app"))
        mine = self.home / "Applications" / "Claude.app"
        mine.mkdir(parents=True)
        self.assertEqual([mine], self._resolve(str(mine)))

    def test_rejects_another_users_install(self):
        """Attributing one user's app to another is the misattribution class #320/#321 closed."""
        self.assertEqual([], self._resolve("/Users/someoneelse/Applications/Claude.app"))

    def test_rejects_trash_and_mounted_volumes(self):
        self.assertEqual([], self._resolve(str(self.home / ".Trash" / "Claude.app")))
        self.assertEqual([], self._resolve("/Volumes/Backup/Applications/Claude.app"))

    def test_rejects_a_symlinked_path_inside_the_home(self):
        """Lexically in scope but redirected out of it — dir_state would follow the link."""
        apps = self.home / "Applications"
        apps.mkdir(parents=True)
        (apps / "Claude.app").symlink_to("/Users/someoneelse/Applications/Claude.app")
        self.assertEqual([], self._resolve(str(apps / "Claude.app")))

    def test_unavailable_spotlight_does_not_fail_the_scan(self):
        self.assertEqual([], self._resolve(None))
        self.assertEqual([], self._resolve(""))

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX mode bits, and root ignores them")
    def test_unreadable_component_is_kept_not_dropped_as_out_of_scope(self):
        """Through the REAL scope filter, not a mock: a component we cannot lstat is
        unknown, and dropping it here would report the clean absence that prunes."""
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        apps = self.home / "Applications"
        (apps / "Claude.app").mkdir(parents=True)
        os.chmod(apps, 0o000)
        try:
            with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[]), \
                    patch(f"{_MAC_MOD}.run_command", return_value=str(apps / "Claude.app")):
                with self.assertRaises(PermissionError):
                    MacOSClaudeCoworkDetector()._find_install_dir(self.home)
            self.assertIn("bundle:unreadable", utils_mod.cowork_probes())
        finally:
            os.chmod(apps, 0o700)

    def test_unreadable_spotlight_hit_does_not_read_as_absent(self):
        """An unreadable in-scope hit leaves presence unknown, so the scan must not
        report a clean absence that permits a prune."""
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        apps = self.home / "Applications"
        (apps / "Claude.app").mkdir(parents=True)
        os.chmod(apps, 0o000)
        try:
            with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[]), \
                    patch(f"{_MAC_MOD}._spotlight_candidates", return_value=[apps / "Claude.app"]):
                with self.assertRaises(PermissionError):
                    MacOSClaudeCoworkDetector()._find_install_dir(self.home)
            self.assertIn("bundle:unreadable", utils_mod.cowork_probes())
        finally:
            os.chmod(apps, 0o700)

    def test_fixed_path_wins_without_asking_spotlight(self):
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        app = self.home / "Applications" / "Claude.app"
        app.mkdir(parents=True)
        with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[app]), \
                patch(f"{_MAC_MOD}.run_command") as mdfind:
            self.assertEqual(app, MacOSClaudeCoworkDetector()._find_install_dir(self.home))
        mdfind.assert_not_called()
        self.assertIn("bundle:present", utils_mod.cowork_probes())

    def test_spotlight_resolves_when_fixed_paths_miss(self):
        from scripts.coding_discovery_tools.macos.claude_cowork.claude_cowork import (
            MacOSClaudeCoworkDetector,
        )
        app = self.home / "Applications" / "Claude.app"
        app.mkdir(parents=True)
        with patch(f"{_MAC_MOD}._candidate_install_dirs", return_value=[self.home / "nope.app"]), \
                patch(f"{_MAC_MOD}.run_command", return_value=str(app)):
            self.assertEqual(app, MacOSClaudeCoworkDetector()._find_install_dir(self.home))
        self.assertIn("bundle:spotlight", utils_mod.cowork_probes())


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
        spotlight = patch(f"{_MAC_MOD}.run_command", return_value=None)
        spotlight.start()
        self.addCleanup(spotlight.stop)

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
