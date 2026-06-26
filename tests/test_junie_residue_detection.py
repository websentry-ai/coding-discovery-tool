"""Residue-vs-real detection tests for Junie (FIX 1).

The fix stops treating the ``~/.junie`` directory as proof of installation.
``~/.junie`` is a user-authored guidelines dir (AGENTS.md / config.json): it
survives an uninstall AND is created by usage rather than install, so gating on
it produced false positives. Detection now gates on a real install signal — the
Junie CLI *binary* (via ``find_junie_binary_for_user``) OR the *Junie plugin*
present in a JetBrains IDE. ``~/.junie`` stays the version source and the
rules/MCP extraction source.

Both routing entry points are exercised:

* ``detect_tool_for_user`` -> ``_detect_junie`` — the production root/MDM
  central path (``user_tool_detector.py``).
* ``find_junie_binary_for_user`` — the per-user binary resolver, including the
  root owner-attribution guard.

Both directions are proven: real binary/plugin -> detected (false-NEGATIVE
guard); residue-only ``~/.junie`` -> NOT detected (the FP fix).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import (
    detect_tool_for_user,
    find_junie_binary_for_user,
)

_MOD = "scripts.coding_discovery_tools.user_tool_detector"
_UTILS = "scripts.coding_discovery_tools.utils"

_HOMEBREW = Path("/opt/homebrew/bin/junie")
_USR_LOCAL = Path("/usr/local/bin/junie")
_ABS_LITERALS = (_HOMEBREW, _USR_LOCAL)


def _isolate_abs(present: Path = None):
    """Mask the two absolute junie literals as absent (so a real install on the
    test host can't leak), except an optional ``present`` target which also
    reports executable. All other paths fall through to the real os/Path."""
    real_exists = Path.exists
    real_access = os.access

    def fake_exists(self):
        if self in _ABS_LITERALS:
            return self == present
        return real_exists(self)

    def fake_access(path, mode):
        if present is not None and str(path) == str(present):
            return True
        if Path(path) in _ABS_LITERALS:
            return False
        return real_access(path, mode)

    return patch("pathlib.Path.exists", fake_exists), patch.object(os, "access", fake_access)


def _stat_for_uid(target: Path, uid: int):
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            return Mock(st_uid=uid)
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _pwd_home(uid_to_home: dict):
    def fake_getpwuid(uid):
        if uid in uid_to_home:
            return Mock(pw_dir=str(uid_to_home[uid]))
        raise KeyError(uid)

    return fake_getpwuid


def _make_junie_detector(plugin_path=None):
    """A junie detector stub for the central ``_detect_junie`` path. The plugin
    signal is delegated via ``_has_junie_jetbrains_plugin``; default None."""
    det = Mock()
    det.tool_name = "Junie"
    det._has_junie_jetbrains_plugin = Mock(return_value=plugin_path)
    return det


def _make_exec(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


class TestJunieCentralPathPosix(unittest.TestCase):
    """``detect_tool_for_user`` -> ``_detect_junie`` (root/MDM path), POSIX."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._install_isolation()

    def tearDown(self):
        self.tmp.cleanup()

    def _install_isolation(self, present: Path = None):
        p_exists, p_access = _isolate_abs(present)
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)

    # --- residue-only: NOT detected --------------------------------------

    def test_residue_junie_dir_only_not_detected(self):
        """Only ``~/.junie`` (with config.json) present, no binary, no plugin
        -> not detected. The central FP fix."""
        cdir = self.home / ".junie"
        cdir.mkdir()
        (cdir / "config.json").write_text('{"version": "1.0.0"}')
        det = _make_junie_detector(plugin_path=None)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = detect_tool_for_user(det, self.home)
        self.assertIsNone(result)

    # --- real binary: detected -------------------------------------------

    def test_local_bin_binary_detected(self):
        junie = _make_exec(self.home / ".local" / "bin" / "junie")
        (self.home / ".junie").mkdir()
        (self.home / ".junie" / "config.json").write_text('{"version": "3.4.5"}')
        det = _make_junie_detector(plugin_path=None)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = detect_tool_for_user(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Junie")
        self.assertEqual(result["install_path"], str(junie))
        self.assertEqual(result["version"], "3.4.5")

    def test_versioned_binary_detected_newest(self):
        base = self.home / ".local" / "share" / "junie" / "versions"
        _make_exec(base / "1.9.0" / "junie")
        newest = _make_exec(base / "1.10.0" / "junie")
        det = _make_junie_detector(plugin_path=None)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = detect_tool_for_user(det, self.home)
        self.assertIsNotNone(result)
        # Numeric version sort picks 1.10.0 over 1.9.0 (not a string sort).
        self.assertEqual(result["install_path"], str(newest))

    # --- plugin signal: detected -----------------------------------------

    def test_jetbrains_plugin_detected_when_no_binary(self):
        det = _make_junie_detector(plugin_path="/cfg/PyCharm2024.1")
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = detect_tool_for_user(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/cfg/PyCharm2024.1")

    def test_version_unknown_when_no_config(self):
        _make_exec(self.home / ".local" / "bin" / "junie")
        det = _make_junie_detector(plugin_path=None)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = detect_tool_for_user(det, self.home)
        self.assertEqual(result["version"], "Unknown")


class TestFindJunieBinaryRootAttribution(unittest.TestCase):
    """``find_junie_binary_for_user`` machine-global owner attribution + guards."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._install_isolation()

    def tearDown(self):
        self.tmp.cleanup()

    def _install_isolation(self, present: Path = None):
        p_exists, p_access = _isolate_abs(present)
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_skipped_when_root_owned_by_other(self):
        """Under root, /opt/homebrew/bin/junie owned by a DIFFERENT user's home
        is skipped (not fanned out); no user-local binary -> None."""
        self._install_isolation(_HOMEBREW)
        other = self.home.parent / "someone_else"
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 502)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({502: other})), \
             patch(f"{_MOD}.run_command", return_value=None):
            self.assertIsNone(find_junie_binary_for_user(self.home))

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_attributed_when_root_owned_by_this_user(self):
        """Under root, /opt/homebrew/bin/junie owned by THIS user's home is
        attributed (returned)."""
        self._install_isolation(_HOMEBREW)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 501)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({501: self.home})), \
             patch(f"{_MOD}.run_command", return_value=None):
            self.assertEqual(find_junie_binary_for_user(self.home), str(_HOMEBREW))

    def test_which_backstop_skipped_when_root(self):
        """The ``which junie`` PATH backstop is skipped under root (it resolves
        the scanner's PATH). With no user-local binary -> None, and run_command
        is never called."""
        run_mock = Mock(return_value=str(self.home / "x" / "junie"))
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", run_mock):
            self.assertIsNone(find_junie_binary_for_user(self.home))
        run_mock.assert_not_called()

    def test_which_backstop_used_when_not_root(self):
        target = _make_exec(self.home / "custom" / "junie")
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=str(target)):
            self.assertEqual(find_junie_binary_for_user(self.home), str(target))


class TestFindJunieBinaryWindows(unittest.TestCase):
    """Windows candidate list (existence-gated, no X_OK semantics)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_bin_exe_detected(self):
        exe = self.home / ".local" / "bin" / "junie.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("")
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            self.assertEqual(find_junie_binary_for_user(self.home), str(exe))

    def test_npm_cmd_detected(self):
        cmd = self.home / "AppData" / "Roaming" / "npm" / "junie.cmd"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text("")
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            self.assertEqual(find_junie_binary_for_user(self.home), str(cmd))

    def test_versioned_exe_detected_newest(self):
        base = self.home / ".local" / "share" / "junie" / "versions"
        (base / "1.9.0").mkdir(parents=True)
        (base / "1.9.0" / "junie.exe").write_text("")
        (base / "1.10.0").mkdir(parents=True)
        newest = base / "1.10.0" / "junie.exe"
        newest.write_text("")
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            self.assertEqual(find_junie_binary_for_user(self.home), str(newest))

    def test_residue_dir_only_returns_none(self):
        (self.home / ".junie").mkdir()
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            self.assertIsNone(find_junie_binary_for_user(self.home))


if __name__ == "__main__":
    unittest.main()
