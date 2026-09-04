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

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import (
    _EXTENSION_VERSION,
    _detect_claude_code,
    find_claude_binary_for_user,
)

_MOD = "scripts.coding_discovery_tools.user_tool_detector"
# The owner-attribution helper (machine_global_binary_owned_by_user) lives in
# utils and reads os.stat(...).st_uid + pwd.getpwuid(uid).pw_dir. W1 tests mock
# those on the utils module so attribution never depends on the real FS owner
# (this dev Mac actually has /opt/homebrew/bin/claude owned by a real user).
_UTILS = "scripts.coding_discovery_tools.utils"

# Absolute-literal candidate paths baked into ``find_claude_binary_for_user``.
_HOMEBREW = Path("/opt/homebrew/bin/claude")
_USR_LOCAL = Path("/usr/local/bin/claude")
_USR_BIN = Path("/usr/bin/claude")


def _stat_for_uid(target: Path, uid: int):
    """os.stat side_effect: return a fake stat (chosen ``uid``) for ``target``,
    pass through to the real os.stat for every other path."""
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            return Mock(st_uid=uid)
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _absent_unless_under(home: Path):
    """Path.exists side_effect hiding machine-global candidates, so a real
    system-wide claude on the test host can't satisfy a user-scoped case."""
    real_exists = Path.exists

    def fake_exists(self, *args, **kwargs):
        try:
            return real_exists(self, *args, **kwargs) and self.is_relative_to(home)
        except (OSError, ValueError):
            return False

    return fake_exists


def _stat_raises_for(target: Path, exc: OSError = None):
    """os.stat side_effect: raise for ``target`` only, pass through otherwise.
    A real stat failure is per-file, not global — scoping it keeps unrelated
    probes working so the test exercises the helper's never-crash path."""
    real_stat = os.stat
    err = exc or OSError("boom")

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            raise err
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _pwd_home(uid_to_home: dict):
    """pwd.getpwuid side_effect mapping uid -> a pw_dir; unknown uid -> KeyError."""

    def fake_getpwuid(uid):
        if uid in uid_to_home:
            return Mock(pw_dir=str(uid_to_home[uid]))
        raise KeyError(uid)

    return fake_getpwuid


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
_ABS_LITERALS = (_HOMEBREW, _USR_LOCAL, _USR_BIN)


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

    @unittest.skipIf(os.name == "nt", "os.chmod/os.access(X_OK) have no POSIX semantics on Windows")
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

    def test_usr_bin_binary_detected_when_not_root(self):
        """FIX #3: ``/usr/bin/claude`` (apt/dnf system package) -> detected when
        NOT root. Added alongside the existing Homebrew / /usr/local literals.

        Fails against the pre-fix candidate list, which omitted ``/usr/bin``."""
        self._with_abs(_USR_BIN)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_USR_BIN))

    def test_usr_bin_binary_skipped_when_root(self):
        """GUARD: ``/usr/bin/claude`` is MACHINE-GLOBAL, so under a root/MDM
        multi-user scan it must be SKIPPED (one shared install must not be
        attributed to every user) — mirrors the Homebrew/usr-local root guard.
        With ``/usr/bin/claude`` "present" but no user_home-relative binary, the
        finder returns None under root."""
        self._with_abs(_USR_BIN)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertIsNone(result)

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

    def test_homebrew_skipped_when_root(self):
        """Under a root/MDM multi-user scan, the MACHINE-GLOBAL Homebrew /
        /usr/local candidates must be SKIPPED — probing them per-user would
        attribute one shared install to EVERY user. With Homebrew "present" but
        no user_home-relative binary, the finder returns None under root. Fails
        against the pre-guard code, which probed Homebrew regardless of root."""
        self._with_abs(_HOMEBREW)  # /opt/homebrew/bin/claude "present"+exec
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertIsNone(result)

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

    # --- W1: machine-global owner attribution under root -----------------
    # Recovers the Homebrew-only false-NEGATIVE from 93b5fc2 WITHOUT
    # re-opening the cross-user false-positive: under root, a machine-global
    # claude binary is attributed to its OWNER (Homebrew/usr-local) or to every
    # scanned user when root-owned (apt/dnf /usr/bin), instead of being dropped.

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_owned_by_this_user_detected_when_root(self):
        """W1: root scan, /opt/homebrew/bin/claude present and owned by a uid
        whose home == the scanned user_home -> attributed (returned). Fails
        against pre-W1 code, which dropped all machine-global candidates under
        root."""
        self._with_abs(_HOMEBREW)  # present + executable
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 501)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({501: self.home})), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertEqual(result, str(_HOMEBREW))

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_owned_by_other_user_not_detected_when_root(self):
        """W1 (the FP guard): root scan, /opt/homebrew/bin/claude owned by a
        DIFFERENT user's home -> skipped; with no user-local binary the finder
        returns None (one user's Homebrew install is not fanned out)."""
        self._with_abs(_HOMEBREW)
        other_home = self.home.parent / "someone_else"
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 502)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({502: other_home})), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertIsNone(result)

    def test_usr_bin_root_owned_detected_when_root(self):
        """W1: root scan, /usr/bin/claude owned by uid 0 (apt/dnf system-wide)
        -> attributed to whoever is being scanned (returned). No pwd lookup is
        needed for uid 0. Fails against pre-W1 code, which dropped /usr/bin
        under root."""
        self._with_abs(_USR_BIN)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_USR_BIN, 0)), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertEqual(result, str(_USR_BIN))

    def test_machine_global_detected_when_not_root_no_owner_check(self):
        """W1: when NOT root, a present machine-global binary is returned
        directly with NO owner check (single-user case unchanged)."""
        self._with_abs(_HOMEBREW)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertEqual(result, str(_HOMEBREW))

    def test_stat_failure_never_crashes_when_root(self):
        """W1: root scan, os.stat on the machine-global candidate raises OSError
        -> helper returns False (skip), candidate dropped, no exception escapes.
        With no user-local binary the finder returns None."""
        self._with_abs(_HOMEBREW)
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_raises_for(_HOMEBREW)), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = find_claude_binary_for_user(self.home)
        self.assertIsNone(result)


class TestClaudeCodeDetectorPosix(unittest.TestCase):
    """The OS detector's own ``detect()`` — the single-user path, which used to
    fall back to ``~/.claude`` while ``_detect_claude_code`` already gated on the
    binary."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _detect(self):
        from scripts.coding_discovery_tools.macos.claude_code.claude_code import MacOSClaudeDetector
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch("pathlib.Path.home", return_value=self.home), \
             patch(f"{_MOD}.run_command", return_value=None), \
             patch.object(Path, "exists", _absent_unless_under(self.home)):
            return MacOSClaudeDetector().detect()

    def test_residue_claude_dir_only_not_detected(self):
        (self.home / ".claude").mkdir()
        self.assertIsNone(self._detect())

    def test_local_bin_binary_detected(self):
        binary = self.home / ".local" / "bin" / "claude"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        os.chmod(binary, 0o755)
        (self.home / ".claude").mkdir()
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    @unittest.skipIf(os.name == "nt", "shell shim is POSIX-only")
    def test_version_comes_from_the_detected_binary(self):
        """The reported version must belong to the binary detection found, not to
        whichever claude an independent search happens to reach first."""
        binary = self.home / ".local" / "bin" / "claude"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
        os.chmod(binary, 0o755)
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "9.9.9")


class TestWindowsClaudeVersionProbe(unittest.TestCase):
    """Windows get_version must probe the resolved binary, and must never hand a
    shim path to cmd.exe, which re-parses the command line."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _command_for(self, binary):
        from scripts.coding_discovery_tools.windows.claude_code import claude_code as mod
        with patch.object(mod, "run_command", return_value="1.2.3") as run:
            mod.WindowsClaudeDetector().get_version(binary)
        return run.call_args.args[0] if run.call_args else None

    def test_exe_is_invoked_directly(self):
        self.assertEqual(self._command_for(r"C:\p\claude.exe"), [r"C:\p\claude.exe", "--version"])

    def test_no_binary_keeps_the_path_search(self):
        self.assertEqual(self._command_for(None), ["cmd", "/c", "claude", "--version"])

    def test_shim_is_never_executed(self):
        """A profile dir may contain a cmd metacharacter (``C:\\Users\\a&b``), so a
        shim path must never reach a shell — no subprocess at all."""
        self.assertIsNone(self._command_for(r"C:\Users\a&b\npm\claude.cmd"))

    def test_shim_version_read_from_package_json(self):
        from scripts.coding_discovery_tools.windows.claude_code.claude_code import WindowsClaudeDetector
        shim = Path(self.tmp.name) / "a&b" / "npm" / "claude.cmd"
        pkg = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text('{"version": "3.2.1"}', encoding="utf-8")
        self.assertEqual(WindowsClaudeDetector().get_version(str(shim)), "3.2.1")

    def test_shim_without_metadata_is_unknown(self):
        from scripts.coding_discovery_tools.windows.claude_code.claude_code import WindowsClaudeDetector
        shim = Path(self.tmp.name) / "npm" / "claude.cmd"
        shim.parent.mkdir(parents=True)
        self.assertIsNone(WindowsClaudeDetector().get_version(str(shim)))


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

    def test_winget_links_shim_detected(self):
        """FIX #3: WinGet (a documented primary installer) drops a shim into the
        per-user ``AppData\\Local\\Microsoft\\WinGet\\Links\\claude.exe`` dir —
        NOT under ``AppData\\Local\\Programs\\claude``. This must now be
        detected on the Windows branch.

        Fails against the pre-fix Windows candidate list, which omitted the
        WinGet Links shim dir."""
        exe = (self.home / "AppData" / "Local" / "Microsoft" / "WinGet"
               / "Links" / "claude.exe")
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

    def test_nvm_windows_shim_detected(self):
        """nvm-windows moves the npm global prefix to ``%APPDATA%\\nvm\\<version>``."""
        cmd = self.home / "AppData" / "Roaming" / "nvm" / "v22.11.0" / "claude.cmd"
        self._make_exec(cmd)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(cmd))

    def test_volta_shim_detected(self):
        exe = self.home / "AppData" / "Local" / "Volta" / "bin" / "claude.exe"
        self._make_exec(exe)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(exe))

    def test_pnpm_shim_detected(self):
        cmd = self.home / "AppData" / "Local" / "pnpm" / "claude.cmd"
        self._make_exec(cmd)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(cmd))

    def test_nvm_dir_with_shell_metacharacters_ignored(self):
        """A dir like ``x&&calc`` would inject a command into the shell=True probe."""
        for name in ("x&&calc", "v1^b", "%SYSTEMROOT%", "node20"):
            with self.subTest(name=name):
                self._make_exec(
                    self.home / "AppData" / "Roaming" / "nvm" / name / "claude.cmd"
                )
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)

    def test_another_users_nvm_shim_not_attributed(self):
        """Every added root is ``user_home``-relative."""
        other = Path(self.tmp.name).parent / "other-profile"
        self._make_exec(other / "AppData" / "Roaming" / "nvm" / "v22.11.0" / "claude.cmd")
        self.addCleanup(shutil.rmtree, other, True)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()


class TestNvmVersionAllowlist(unittest.TestCase):
    """Asserted on the pattern, not the filesystem: Windows cannot create a dir whose
    name holds a newline, so the ``\\Z`` anchor is untestable through mkdir there."""

    def test_accepts_real_version_dirs(self):
        for name in ("v22.11.0", "20.9.0", "v18", "1.2.3.4"):
            with self.subTest(name=name):
                self.assertTrue(utils_mod._NVM_WINDOWS_VERSION_DIR.match(name))

    def test_rejects_trailing_newline(self):
        """Python ``$`` also matches before a trailing newline; ``\\Z`` does not."""
        for name in ("v22.11.0\n", "20.9.0\n", "v1\n&&calc"):
            with self.subTest(name=name):
                self.assertIsNone(utils_mod._NVM_WINDOWS_VERSION_DIR.match(name))

    def test_rejects_shell_metacharacters(self):
        for name in ("x&&calc", "v1^b", "%SYSTEMROOT%", "node20", "v1;rm", "v1 v2"):
            with self.subTest(name=name):
                self.assertIsNone(utils_mod._NVM_WINDOWS_VERSION_DIR.match(name))


class TestToolConfigDirsDiagnostic(unittest.TestCase):
    """Diagnostic only. On a ZERO-tool scan these dirs separate "machine has no AI
    tooling" from "a tool ran here and we missed its binary". Never a detection gate
    — that is what the residue tests above pin."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_config_dirs(self):
        self.assertEqual([], utils_mod.tool_config_dirs_present(self.home))

    def test_reports_each_tool_dir(self):
        for d in (".claude", ".copilot", ".cursor"):
            (self.home / d).mkdir()
        self.assertEqual(["claude", "copilot", "cursor"],
                         utils_mod.tool_config_dirs_present(self.home))

    def test_ignores_files_and_unknown_dirs(self):
        (self.home / ".claude").write_text("")      # file, not a dir
        (self.home / ".notatool").mkdir()
        self.assertEqual([], utils_mod.tool_config_dirs_present(self.home))

    def test_missing_home_never_raises(self):
        self.assertEqual([], utils_mod.tool_config_dirs_present(self.home / "nope"))

    def test_is_a_queryable_sentry_tag(self):
        """Must be a tag, not just context, or it can't be grouped on in Sentry."""
        self.assertIn("config_dirs_present", utils_mod._SENTRY_TAG_KEYS)


class TestClaudeCodeVSCodeExtensionBinary(unittest.TestCase):
    """The extension bundles the CLI at ``resources/native-binary/claude`` — a real
    binary install no fixed candidate path reaches. Gated on a live
    ``extensions.json`` entry, and probed last."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _plant(self, ext_root: str, exe: str, listed: bool = True, with_binary: bool = True) -> Path:
        """Install the extension under ``<home>/<ext_root>``; returns the bundled binary."""
        ext_dir = self.home / ext_root / "anthropic.claude-code-2.1.260"
        binary = ext_dir / "resources" / "native-binary" / exe
        if with_binary:
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("")
            os.chmod(binary, 0o755)
        else:
            ext_dir.mkdir(parents=True, exist_ok=True)
        entries = [{"identifier": {"id": "anthropic.claude-code"}, "version": "2.1.260",
                    "relativeLocation": ext_dir.name}] if listed else []
        registry = self.home / ext_root / "extensions.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps(entries))
        return binary

    def _detect_windows(self):
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            return _detect_claude_code(det, self.home)

    def test_bundled_binary_detected_in_vscode(self):
        binary = self._plant(".vscode/extensions", "claude.exe")
        result = self._detect_windows()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_bundled_binary_detected_in_forks(self):
        for ext_root in (".cursor/extensions", ".windsurf/extensions",
                         ".vscode-oss/extensions", ".antigravity/extensions"):
            with self.subTest(ext_root=ext_root):
                self.tearDown()
                self.setUp()
                binary = self._plant(ext_root, "claude.exe")
                self.assertEqual(self._detect_windows()["install_path"], str(binary))

    def test_uninstalled_extension_residue_not_detected(self):
        """Uninstall: dir and binary still on disk, but delisted from the registry."""
        self._plant(".vscode/extensions", "claude.exe", listed=False)
        self.assertIsNone(self._detect_windows())

    def test_listed_extension_without_binary_not_detected(self):
        self._plant(".vscode/extensions", "claude.exe", with_binary=False)
        self.assertIsNone(self._detect_windows())

    def test_standalone_install_wins_over_extension(self):
        """Both surfaces present -> one row, pointing at the standalone install."""
        self._plant(".vscode/extensions", "claude.exe")
        npm = self.home / "AppData" / "Roaming" / "npm" / "claude.cmd"
        npm.parent.mkdir(parents=True, exist_ok=True)
        npm.write_text("")
        os.chmod(npm, 0o755)
        self.assertEqual(self._detect_windows()["install_path"], str(npm))

    def test_registry_location_is_never_used(self):
        """The recorded location is a VS Code URI (``/c:/Users/...`` on Windows) and is
        user-writable while the binary it names is executed, so it must not be followed:
        the real install still resolves, and a location pointing elsewhere is ignored."""
        real = self._plant(".vscode/extensions", "claude.exe")
        decoy = self.home / "elsewhere" / "resources" / "native-binary" / "claude.exe"
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("")
        os.chmod(decoy, 0o755)
        registry = self.home / ".vscode" / "extensions" / "extensions.json"
        registry.write_text(json.dumps([{
            "identifier": {"id": "anthropic.claude-code"},
            "version": "2.1.260",
            "location": {"$mid": 1, "path": "/c:/nope", "scheme": "file"},
            "relativeLocation": "anthropic.claude-code-2.1.260",
        }]))
        self.assertEqual(self._detect_windows()["install_path"], str(real))

    def test_platform_suffixed_install_dir_detected(self):
        """Real dirs carry a platform suffix: anthropic.claude-code-2.1.217-darwin-arm64."""
        ext_root = self.home / ".vscode" / "extensions"
        binary = ext_root / "anthropic.claude-code-2.1.260-win32-x64" / "resources" / "native-binary" / "claude.exe"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("")
        os.chmod(binary, 0o755)
        ext_root.joinpath("extensions.json").write_text(json.dumps([{
            "identifier": {"id": "anthropic.claude-code"}, "version": "2.1.260",
            "relativeLocation": "anthropic.claude-code-2.1.260-win32-x64",
        }]))
        self.assertEqual(self._detect_windows()["install_path"], str(binary))

    def test_corrupt_registry_never_raises(self):
        registry = self.home / ".vscode" / "extensions" / "extensions.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text("{not json")
        self.assertIsNone(self._detect_windows())

    @unittest.skipIf(os.name == "nt", "POSIX branch: os.access(X_OK) has no Windows semantics")
    def test_bundled_binary_detected_on_posix(self):
        """Same surface on macOS/Linux, where the bundled binary has no suffix."""
        binary = self._plant(".vscode/extensions", "claude")
        p_exists, p_access = _isolate_abs()
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_claude_code(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_live_version_selected_over_superseded_dirs(self):
        """Superseded version dirs keep working binaries, so the registry's live
        version must pick the dir actually in use."""
        ext_root = self.home / ".vscode" / "extensions"
        for ver in ("2.1.187", "2.1.217", "2.1.202"):
            b = ext_root / f"anthropic.claude-code-{ver}-win32-x64" / "resources" / "native-binary" / "claude.exe"
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_text("")
            os.chmod(b, 0o755)
        ext_root.joinpath("extensions.json").write_text(json.dumps([{
            "identifier": {"id": "anthropic.claude-code"}, "version": "2.1.217",
            "relativeLocation": "anthropic.claude-code-2.1.217-win32-x64",
        }]))
        self.assertIn("2.1.217", self._detect_windows()["install_path"])

    def test_glob_metacharacters_in_version_rejected(self):
        """The registry version reaches a glob pattern and is user-writable."""
        for bad in ("../../evil", "2.1.*", "2.1.260\n", "*"):
            with self.subTest(version=bad):
                self.assertIsNone(_EXTENSION_VERSION.match(bad))
        for good in ("2.1.260", "2", "2.1.260.1"):
            with self.subTest(version=good):
                self.assertIsNotNone(_EXTENSION_VERSION.match(good))
