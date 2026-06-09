"""Residue-vs-real detection tests for Claude Code.

The fix stops treating the ``~/.claude`` config directory as proof of
installation. ``~/.claude`` survives an uninstall (it holds settings, MCP
config, history), so gating on it produced false positives. Detection now
gates on the claude *binary* via ``find_claude_binary_for_user`` (Homebrew,
``~/.local/bin``, ``~/.bun/bin``, ``~/.npm-global/bin``, yarn-global, nvm, and
a ``which claude`` PATH backstop; Windows: npm ``.cmd``/``.exe``, Programs,
``.bun/bin/claude.exe``).

Both directions are proven: each real binary location -> detected (the
false-NEGATIVE guard); residue-only ``~/.claude`` -> NOT detected (the FP fix);
and a present-but-non-executable file -> NOT detected.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import (
    _detect_claude_code,
    find_claude_binary_for_user,
)

_MOD = "scripts.coding_discovery_tools.user_tool_detector"

# Absolute-literal candidate paths baked into ``find_claude_binary_for_user``.
_HOMEBREW = Path("/opt/homebrew/bin/claude")
_USR_LOCAL = Path("/usr/local/bin/claude")


def _make_detector():
    det = Mock()
    det.tool_name = "Claude Code"
    det.get_version.return_value = "1.0.0"
    return det


# The two absolute-literal candidates are probed BEFORE the HOME candidates,
# so a real claude install on the test host (e.g. Homebrew on a dev Mac) would
# shadow the fake-HOME binary and defeat isolation. ``_isolate_abs`` masks both
# absolute literals as absent — except one optionally-present target — while
# every other path keeps real behaviour. This keeps the suite hermetic on any
# host regardless of what is actually installed.
_ABS_LITERALS = (_HOMEBREW, _USR_LOCAL)


def _isolate_abs(present: Path = None):
    """Return (exists_patch, access_patch). The two absolute claude literals
    report absent unless equal to ``present``; ``present`` (if given) also
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


class TestClaudeCodeResidueDetectionPosix(unittest.TestCase):
    """macOS/Linux: ``platform.system() != 'Windows'`` candidate list."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Default: mask both absolute claude literals as absent so a real
        # install on the test host cannot leak into HOME-binary cases. The two
        # positive Homebrew/usr-local tests override via ``_with_abs``.
        self._exit_isolation()

    def tearDown(self):
        self.tmp.cleanup()

    def _exit_isolation(self, present: Path = None):
        """(Re)install the absolute-literal isolation, masking the previous one
        via ``addCleanup`` so each test starts from a clean host-independent
        baseline."""
        p_exists, p_access = _isolate_abs(present)
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)

    def _with_abs(self, present: Path):
        """Re-point isolation so exactly ``present`` (one absolute literal)
        looks installed+executable for this test."""
        self._exit_isolation(present)

    def _make_exec(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    # --- residue-only: NOT detected -------------------------------------

    def test_residue_claude_dir_only_not_detected_via_detector(self):
        """``_detect_claude_code`` returns None when only ``~/.claude`` exists."""
        cdir = self.home / ".claude"
        cdir.mkdir()
        (cdir / "settings.json").write_text("{}")
        (cdir / ".credentials.json").write_text("{}")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)
        det.get_version.assert_not_called()

    def test_residue_claude_dir_only_binary_finder_returns_none(self):
        (self.home / ".claude").mkdir()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            self.assertIsNone(find_claude_binary_for_user(self.home))

    def test_non_executable_binary_not_detected(self):
        """A ``claude`` file that exists but is NOT ``os.X_OK`` -> None.
        Proves detection requires a real *executable*, not just a file."""
        claude = self.home / ".local" / "bin" / "claude"
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text("#!/bin/sh\n")
        os.chmod(claude, 0o644)  # readable, not executable
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)

    # --- real binary locations: detected --------------------------------

    def test_homebrew_binary_detected(self):
        self._with_abs(_HOMEBREW)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_HOMEBREW))
        self.assertEqual(result["name"], "Claude Code")

    def test_usr_local_binary_detected(self):
        self._with_abs(_USR_LOCAL)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_USR_LOCAL))

    def test_local_bin_binary_detected(self):
        self._make_exec(self.home / ".local" / "bin" / "claude")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.home / ".local" / "bin" / "claude"))

    def test_bun_binary_detected(self):
        self._make_exec(self.home / ".bun" / "bin" / "claude")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.home / ".bun" / "bin" / "claude"))

    def test_npm_global_binary_detected(self):
        self._make_exec(self.home / ".npm-global" / "bin" / "claude")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.home / ".npm-global" / "bin" / "claude"))

    def test_yarn_global_binary_detected(self):
        yarn = (self.home / ".config" / "yarn" / "global"
                / "node_modules" / ".bin" / "claude")
        self._make_exec(yarn)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(yarn))

    def test_nvm_binary_detected(self):
        nvm = self.home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin" / "claude"
        self._make_exec(nvm)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(nvm))

    def test_which_backstop_detected(self):
        """No HOME/Homebrew binary, but ``which claude`` resolves to a real
        executable -> detected. Proves the PATH backstop (non-root case)."""
        which_target = self.home / "custom" / "claude"
        self._make_exec(which_target)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=str(which_target)):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(which_target))

    def test_which_backstop_ignores_nonexistent_path(self):
        """``which claude`` returning a path that doesn't exist must not
        produce a detection (guards against stale shell hash entries)."""
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value="/nope/claude"):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)

    def test_which_backstop_skipped_when_root(self):
        """INFO: under a root/MDM multi-user scan, the ``which claude`` PATH
        backstop must be SKIPPED — it resolves the SCANNER's PATH (root's
        claude), not ``user_home``'s, so honouring it mis-attributes an
        install to a user who has none. With no user_home-relative binary and
        no absolute literal, the finder must return None even though ``which``
        would resolve to a real executable.

        Asserts ``run_command`` is never called (the backstop is skipped
        entirely), so this is robust to how ``which`` is wired.

        Fails against the pre-fix code, which ran ``which`` regardless of
        root."""
        which_target = self.home / "root_path" / "claude"
        self._make_exec(which_target)  # a real exe ``which`` would resolve to
        run_mock = Mock(return_value=str(which_target))
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", run_mock):
            result = find_claude_binary_for_user(self.home)
        self.assertIsNone(result)
        run_mock.assert_not_called()

    def test_user_home_binary_still_found_when_root(self):
        """Under root, a genuine ``user_home``-relative binary IS still found
        (the explicit candidate list is user_home-relative and unaffected by
        the root skip — only the scanner-PATH ``which`` backstop is gated)."""
        self._make_exec(self.home / ".local" / "bin" / "claude")
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertEqual(result, str(self.home / ".local" / "bin" / "claude"))

    def test_residue_dir_plus_real_binary_uses_binary(self):
        (self.home / ".claude").mkdir()
        self._make_exec(self.home / ".local" / "bin" / "claude")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertNotEqual(result["install_path"], str(self.home / ".claude"))
        self.assertEqual(result["install_path"], str(self.home / ".local" / "bin" / "claude"))


class TestClaudeCodeResidueDetectionWindows(unittest.TestCase):
    """Windows: ``platform.system() == 'Windows'`` candidate list."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_exec(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        os.chmod(path, 0o755)
        return path

    def test_residue_claude_dir_only_not_detected(self):
        (self.home / ".claude").mkdir()
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)

    def test_npm_cmd_binary_detected(self):
        cmd = self.home / "AppData" / "Roaming" / "npm" / "claude.cmd"
        self._make_exec(cmd)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(cmd))

    def test_npm_exe_binary_detected(self):
        exe = self.home / "AppData" / "Roaming" / "npm" / "claude.exe"
        self._make_exec(exe)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(exe))

    def test_programs_binary_detected(self):
        exe = self.home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe"
        self._make_exec(exe)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(exe))

    def test_bun_exe_binary_detected(self):
        exe = self.home / ".bun" / "bin" / "claude.exe"
        self._make_exec(exe)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(exe))


if __name__ == "__main__":
    unittest.main()
