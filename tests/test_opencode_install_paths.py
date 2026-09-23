"""Install-path detection tests for OpenCode (macOS + Linux).

The original detector only ran ``which opencode``. Users who installed via the
curl script (``~/.opencode/bin``), bun, ``~/.local/bin`` or the desktop app
bundle were invisible. These tests pin every fallback and the no-regression
case where PATH still wins.

The macOS bundle fixture lives inside a temp dir with ``MACHINE_APPS_DIR`` and
the module-level ``_APP_BUNDLE`` patched, so the test never touches the real
``/Applications`` and runs on Linux CI unchanged.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.opencode.opencode import LinuxOpenCodeDetector
from scripts.coding_discovery_tools.macos.opencode.opencode import MacOSOpenCodeDetector

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"
_MAC_MOD = "scripts.coding_discovery_tools.macos.opencode.opencode"
_LINUX_MOD = "scripts.coding_discovery_tools.linux.opencode.opencode"


def _make_exec(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _no_which(cmd, *_args, **_kwargs):
    """run_command stub: PATH lookup finds nothing, --version reports 1.18.31."""
    if cmd[0] == "which":
        return ""
    if cmd[-1] == "--version":
        return "1.18.31\n"
    return ""


class TestMacOSOpenCodeInstallPaths(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.machine_apps = root / "Applications"
        self.machine_apps.mkdir(parents=True)
        self.home = root / "Users" / "alice"
        (self.home / "Applications").mkdir(parents=True)
        self.detector = MacOSOpenCodeDetector()
        self.detector.user_home = self.home
        self._patches = [
            patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps),
            patch(f"{_MAC_MOD}._APP_BUNDLE", self.machine_apps / "OpenCode.app"),
            patch(f"{_MAC_MOD}.run_command", side_effect=_no_which),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _write_bundle(self, bundle: Path, version: str = "1.2.3") -> Path:
        contents = bundle / "Contents"
        contents.mkdir(parents=True)
        with open(contents / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
        return bundle

    def test_curl_installer_path(self):
        bin_path = _make_exec(self.home / ".opencode" / "bin" / "opencode")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "OpenCode")
        self.assertEqual(result["install_path"], str(bin_path))
        self.assertEqual(result["version"], "1.18.31")

    def test_bun_path(self):
        bin_path = _make_exec(self.home / ".bun" / "bin" / "opencode")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bin_path))

    def test_local_bin_path(self):
        bin_path = _make_exec(self.home / ".local" / "bin" / "opencode")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bin_path))

    def test_non_executable_file_is_ignored(self):
        f = self.home / ".local" / "bin" / "opencode"
        f.parent.mkdir(parents=True)
        f.write_text("not a binary", encoding="utf-8")
        f.chmod(0o644)
        self.assertIsNone(self.detector.detect())

    def test_user_applications_bundle(self):
        bundle = self._write_bundle(self.home / "Applications" / "OpenCode.app", "1.4.0")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bundle))
        self.assertEqual(result["version"], "1.4.0")

    def test_machine_applications_bundle(self):
        bundle = self._write_bundle(self.machine_apps / "OpenCode.app", "1.5.0")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bundle))
        self.assertEqual(result["version"], "1.5.0")

    def test_binary_beats_bundle(self):
        bin_path = _make_exec(self.home / ".opencode" / "bin" / "opencode")
        self._write_bundle(self.home / "Applications" / "OpenCode.app")
        result = self.detector.detect()
        self.assertEqual(result["install_path"], str(bin_path))

    def test_which_still_wins(self):
        path_hit = _make_exec(self.home / "somewhere" / "opencode")
        _make_exec(self.home / ".opencode" / "bin" / "opencode")

        def which_hit(cmd, *_a, **_k):
            if cmd[0] == "which":
                return f"{path_hit}\n"
            if cmd[-1] == "--version":
                self.assertEqual(cmd[0], "opencode", "PATH hit must probe bare 'opencode'")
                return "2.0.0\n"
            return ""

        with patch(f"{_MAC_MOD}.run_command", side_effect=which_hit):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(path_hit))
        self.assertEqual(result["version"], "2.0.0")

    def test_fallback_hit_probes_resolved_binary(self):
        bin_path = _make_exec(self.home / ".bun" / "bin" / "opencode")
        seen = []

        def record(cmd, *_a, **_k):
            if cmd[0] == "which":
                return ""
            seen.append(cmd[0])
            return "1.0.0\n"

        with patch(f"{_MAC_MOD}.run_command", side_effect=record):
            self.detector.detect()
        self.assertEqual(seen, [str(bin_path)])

    def test_nothing_present(self):
        self.assertIsNone(self.detector.detect())


class TestLinuxOpenCodeInstallPaths(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home" / "alice"
        self.home.mkdir(parents=True)
        self.detector = LinuxOpenCodeDetector()
        self.detector.user_home = self.home
        self._rc = patch(f"{_LINUX_MOD}.run_command", side_effect=_no_which)
        self._rc.start()

    def tearDown(self):
        self._rc.stop()
        self.tmp.cleanup()

    def test_curl_installer_path(self):
        bin_path = _make_exec(self.home / ".opencode" / "bin" / "opencode")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bin_path))
        self.assertEqual(result["version"], "1.18.31")

    def test_bun_path(self):
        bin_path = _make_exec(self.home / ".bun" / "bin" / "opencode")
        self.assertEqual(self.detector.detect()["install_path"], str(bin_path))

    def test_local_bin_path(self):
        bin_path = _make_exec(self.home / ".local" / "bin" / "opencode")
        self.assertEqual(self.detector.detect()["install_path"], str(bin_path))

    def test_user_home_scoping_ignores_other_homes(self):
        other = Path(self.tmp.name) / "home" / "bob"
        _make_exec(other / ".local" / "bin" / "opencode")
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[other]):
            self.assertIsNone(self.detector.detect())

    def test_falls_back_to_all_homes_when_unscoped(self):
        other = Path(self.tmp.name) / "home" / "bob"
        bin_path = _make_exec(other / ".opencode" / "bin" / "opencode")
        self.detector.user_home = None
        with patch(f"{_LINUX_MOD}.get_linux_user_homes", return_value=[other]):
            self.assertEqual(self.detector.detect()["install_path"], str(bin_path))

    def test_nothing_present(self):
        self.assertIsNone(self.detector.detect())


if __name__ == "__main__":
    unittest.main()
