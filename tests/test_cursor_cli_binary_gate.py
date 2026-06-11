"""Binary-gate tests for Cursor CLI detection.

Cursor CLI (``cursor-agent``) detection gates on the binary, not the
``~/.cursor/cli-config.json`` residue. These drive the live per-user entry point
``detect_tool_for_user`` and pin:

  (a) ``cli-config.json`` only, no binary -> None (the residue FP)
  (b) executable ``~/.local/bin/cursor-agent`` -> detected, install_path is the binary
  (c) only the IDE ``cursor`` launcher on PATH -> None (gating on ``cursor-agent``
      must not pick up the IDE ``cursor`` binary)

The npm-global resolver and ``which`` PATH backstop are neutralised and
``is_running_as_root`` pinned False, so the gate depends only on the binary the
test creates under the hermetic home (else a CI box with a real ``cursor-agent``
on PATH leaks in).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.user_tool_detector as utd
from scripts.coding_discovery_tools.macos.cursor_cli.cursor_cli import MacOSCursorCliDetector

_MOD = "scripts.coding_discovery_tools.user_tool_detector"
# The cursor_cli detector module whose ``run_command`` the version probe calls.
_CURSOR_MOD = "scripts.coding_discovery_tools.macos.cursor_cli.cursor_cli"
# The Windows cursor_cli detector module (its version probe uses ``subprocess``).
_WIN_CURSOR_MOD = "scripts.coding_discovery_tools.windows.cursor_cli.cursor_cli"


class TestCursorCliBinaryGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.detector = MacOSCursorCliDetector()
        # Neutralise the non-filesystem resolvers + pin non-root so only the
        # per-user on-disk binary the test creates can satisfy the gate.
        self._patchers = [
            patch(f"{_MOD}.is_running_as_root", return_value=False),
            patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=None),
            patch(f"{_MOD}.run_command", return_value=None),  # neutralise `which`
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self.tmp.cleanup()

    def _detect(self):
        return utd.detect_tool_for_user(self.detector, self.home)

    def test_cli_config_only_without_binary_not_detected(self):
        """``~/.cursor/cli-config.json`` residue but NO ``cursor-agent`` binary ->
        None (the FP this fix kills)."""
        cursor_dir = self.home / ".cursor"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "cli-config.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(self._detect())

    @unittest.skipIf(os.name == "nt", "POSIX X_OK semantics; Windows .exe path differs")
    def test_local_bin_cursor_agent_detected(self):
        """An executable ``~/.local/bin/cursor-agent`` -> detected; install_path is
        the binary."""
        binary = self.home / ".local" / "bin" / "cursor-agent"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho cursor-agent\n", encoding="utf-8")
        os.chmod(binary, 0o755)
        with patch.object(self.detector, "get_version", return_value=None):
            result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Cursor CLI")
        self.assertEqual(result["install_path"], str(binary))

    @unittest.skipIf(os.name == "nt", "POSIX X_OK semantics; Windows .exe path differs")
    def test_versioned_install_dir_cursor_agent_detected(self):
        """The versioned ``~/.local/share/cursor-agent/versions/<v>/cursor-agent``
        install -> detected."""
        versioned = (self.home / ".local" / "share" / "cursor-agent"
                     / "versions" / "2026.02.13" / "cursor-agent")
        versioned.parent.mkdir(parents=True)
        versioned.write_text("#!/bin/sh\necho x\n", encoding="utf-8")
        os.chmod(versioned, 0o755)
        with patch.object(self.detector, "get_version", return_value=None):
            result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(versioned))

    @unittest.skipIf(os.name == "nt", "POSIX X_OK semantics; Windows .exe path differs")
    def test_versioned_install_picks_numeric_newest_not_lexical(self):
        """With multiple versioned installs, the NEWEST by numeric version wins.
        Regression: a lexical sort orders ``1.9.0`` after ``1.10.0`` and would
        report the stale ``1.9.0`` binary; the numeric key picks ``1.10.0``."""
        base = self.home / ".local" / "share" / "cursor-agent" / "versions"
        for ver in ("1.9.0", "1.10.0", "1.2.0"):
            b = base / ver / "cursor-agent"
            b.parent.mkdir(parents=True)
            b.write_text("#!/bin/sh\necho x\n", encoding="utf-8")
            os.chmod(b, 0o755)
        resolved = utd.find_cursor_agent_binary_for_user(self.home)
        self.assertEqual(resolved, str(base / "1.10.0" / "cursor-agent"))

    def test_only_cursor_launcher_on_path_not_detected(self):
        """Only the Cursor IDE ``cursor`` launcher resolvable on PATH (no
        ``cursor-agent`` anywhere) -> None. The mis-detect guard: gating on
        ``cursor-agent`` must NOT pick up the IDE ``cursor`` binary.

        ``run_command(["which", "cursor-agent"])`` returns None (no agent), and a
        hypothetical ``which cursor`` is never consulted by the gate."""
        def fake_which(cmd, *a, **k):
            # Only the IDE launcher resolves; cursor-agent does not.
            if cmd[:2] == ["which", "cursor"] and cmd[-1] == "cursor":
                return "/usr/local/bin/cursor"
            return None

        with patch(f"{_MOD}.run_command", side_effect=fake_which):
            self.assertIsNone(self._detect())

    def test_nothing_present_not_detected(self):
        self.assertIsNone(self._detect())


class TestCursorCliBinaryGateWindows(unittest.TestCase):
    """Windows branch of ``find_cursor_agent_binary_for_user``.

    The native Windows installer drops ``cursor-agent``/``agent`` (``.exe``/``.cmd``)
    at the root of ``%LOCALAPPDATA%\\cursor-agent`` and keeps the real binary under a
    ``versions\\<v>\\`` subdir; the Git-Bash variant drops an extensionless
    ``~/.local/bin/cursor-agent``. These pin all of those.

    The branch is existence-gated (on Windows ``os.access(X_OK)`` is True for any
    file), so these tests create plain files and never chmod. ``platform.system``
    is pinned to ``"Windows"`` so the branch runs on a POSIX CI box.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _resolve(self):
        with patch(f"{_MOD}.platform.system", return_value="Windows"):
            return utd.find_cursor_agent_binary_for_user(self.home)

    def test_localappdata_cursor_agent_exe_detected(self):
        """``%LOCALAPPDATA%\\cursor-agent\\cursor-agent.exe`` (native installer)
        -> detected."""
        exe = self.home / "AppData" / "Local" / "cursor-agent" / "cursor-agent.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        self.assertEqual(self._resolve(), str(exe))

    def test_localappdata_agent_cmd_detected(self):
        """The ``agent.cmd`` shim variant at the install-dir root -> detected."""
        cmd = self.home / "AppData" / "Local" / "cursor-agent" / "agent.cmd"
        cmd.parent.mkdir(parents=True)
        cmd.write_text("", encoding="utf-8")
        self.assertEqual(self._resolve(), str(cmd))

    def test_versioned_subdir_detected_and_numeric_newest(self):
        """The real binary under ``cursor-agent\\versions\\<v>\\cursor-agent.exe``
        -> detected, and the NEWEST by numeric version wins (a lexical sort would
        pick the stale ``1.9.0`` over ``1.10.0``)."""
        base = self.home / "AppData" / "Local" / "cursor-agent" / "versions"
        for ver in ("1.9.0", "1.10.0", "1.2.0"):
            exe = base / ver / "cursor-agent.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
        self.assertEqual(self._resolve(), str(base / "1.10.0" / "cursor-agent.exe"))

    def test_git_bash_extensionless_detected(self):
        """The Git-Bash (MINGW64) variant drops an extensionless
        ``~/.local/bin/cursor-agent`` -> detected."""
        binary = self.home / ".local" / "bin" / "cursor-agent"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
        self.assertEqual(self._resolve(), str(binary))

    def test_cursor_residue_only_not_detected(self):
        """``~/.cursor/cli-config.json`` residue but no Windows binary -> None."""
        cursor_dir = self.home / ".cursor"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "cli-config.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(self._resolve())

    def test_nothing_present_not_detected(self):
        self.assertIsNone(self._resolve())


class TestCursorCliVersionFromResolvedBinary(unittest.TestCase):
    """Version is probed from the RESOLVED ``cursor-agent`` binary, not a bare
    ``cursor-agent --version`` against the scanner's PATH.

    Under a root MDM scan the user's ``~/.local/bin/cursor-agent`` is not on root's
    PATH, so a bare probe reads nothing and version is "Unknown". Simulated by
    mocking ``run_command`` to return the banner only for the resolved absolute
    path, and None for a bare ``cursor-agent`` (or ``which``).
    """

    _BANNER = "2026.02.13 (Cursor Agent)"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.detector = MacOSCursorCliDetector()
        # Pin non-root for the resolver seam and neutralise the npm-global
        # resolver so only the on-disk per-user binary satisfies the gate.
        self._patchers = [
            patch(f"{_MOD}.is_running_as_root", return_value=False),
            patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=None),
            # The gate's ``which cursor-agent`` backstop in user_tool_detector
            # must not leak a real PATH cursor-agent on the CI box.
            patch(f"{_MOD}.run_command", return_value=None),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self.tmp.cleanup()

    def _make_binary(self) -> Path:
        binary = self.home / ".local" / "bin" / "cursor-agent"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho x\n", encoding="utf-8")
        os.chmod(binary, 0o755)
        return binary

    @unittest.skipIf(os.name == "nt", "POSIX X_OK gate for the ~/.local/bin stub")
    def test_version_resolved_from_binary_when_bare_command_off_path(self):
        """Bare ``cursor-agent`` not on PATH, but the resolved binary IS probed ->
        version is the parsed banner, NOT "Unknown" (the root-scan fix)."""
        binary = self._make_binary()

        def fake_run(command, *a, **k):
            # Banner only for the resolved absolute path; a bare ``cursor-agent``
            # yields nothing, like root's PATH on an MDM scan.
            if command[:1] == [str(binary)]:
                return self._BANNER
            return None

        with patch(f"{_CURSOR_MOD}.run_command", side_effect=fake_run):
            result = utd.detect_tool_for_user(self.detector, self.home)

        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))
        # Version comes from probing the resolved binary, not "Unknown".
        self.assertEqual(result["version"], "2026.02.13")

    @unittest.skipIf(os.name == "nt", "POSIX X_OK gate for the ~/.local/bin stub")
    def test_pre_fix_bare_probe_would_have_been_unknown(self):
        """Non-vacuity guard: with the SAME mock, a bare ``get_version()`` yields
        None -> the row would be "Unknown"; passing the binary yields the version."""
        binary = self._make_binary()

        def fake_run(command, *a, **k):
            if command[:1] == [str(binary)]:
                return self._BANNER
            return None

        with patch(f"{_CURSOR_MOD}.run_command", side_effect=fake_run):
            # No-arg -> bare ``cursor-agent`` -> None.
            self.assertIsNone(self.detector.get_version())
            # Binary arg -> resolved binary probed -> parsed version.
            self.assertEqual(self.detector.get_version(str(binary)), "2026.02.13")

    def test_get_version_no_arg_still_probes_bare_command(self):
        """Back-compat: ``get_version()`` with no arg still probes the bare
        ``cursor-agent --version`` (so any other no-arg caller is unaffected)."""
        with patch(f"{_CURSOR_MOD}.run_command", return_value=self._BANNER) as run:
            version = self.detector.get_version()
        self.assertEqual(version, "2026.02.13")
        self.assertEqual(run.call_args.args[0], ["cursor-agent", "--version"])


@unittest.skipIf(os.name == "nt", "POSIX-only: exercises the macOS Cursor detector probe")
class TestCursorCliVersionProbeShape(unittest.TestCase):
    """The macOS detector probes the EXACT resolved path when given a binary."""

    def test_get_version_with_binary_probes_that_exact_path(self):
        binary = "/Users/someone/.local/bin/cursor-agent"
        with patch(f"{_CURSOR_MOD}.run_command", return_value="1.2.3 (Cursor Agent)") as run:
            version = MacOSCursorCliDetector().get_version(binary)
        self.assertEqual(version, "1.2.3")
        self.assertEqual(run.call_args.args[0], [binary, "--version"])


class TestWindowsCursorCliVersion(unittest.TestCase):
    """Windows Cursor version probe (``subprocess.run(..., shell=True)`` for the
    ``.cmd`` shim).

    The Windows detector class runs on any OS (its ``get_version`` only touches
    ``subprocess``, which we mock), so no ``platform.system`` pin is needed. The
    quoted-path case matters because under ``shell=True`` a bare argv list with a
    path containing spaces (``C:\\Users\\First Last\\...``) is split by cmd.exe, so
    the detector passes a single ``list2cmdline``-quoted command string instead.
    """

    def setUp(self):
        from scripts.coding_discovery_tools.windows.cursor_cli.cursor_cli import (
            WindowsCursorCliDetector,
        )
        self.Detector = WindowsCursorCliDetector

    def test_version_from_resolved_binary_with_spaces_is_quoted(self):
        """An absolute binary path WITH SPACES is passed as a single properly
        quoted command string under shell=True (not a bare list that cmd.exe
        would split), and the version parses."""
        binary = r"C:\Users\First Last\AppData\Local\cursor-agent\cursor-agent.exe"
        fake = MagicMock(returncode=0, stdout="2026.02.13 (Cursor Agent)", stderr="")
        with patch(f"{_WIN_CURSOR_MOD}.subprocess.run", return_value=fake) as run:
            version = self.Detector().get_version(binary)
        self.assertEqual(version, "2026.02.13")
        sent = run.call_args.args[0]
        # A single command STRING (not a list), with the spaced path quoted the
        # way cmd.exe expects.
        self.assertIsInstance(sent, str)
        self.assertEqual(sent, subprocess.list2cmdline([binary, "--version"]))
        self.assertIn(f'"{binary}"', sent)
        self.assertIs(run.call_args.kwargs.get("shell"), True)

    def test_version_no_arg_uses_bare_list_shell_true(self):
        """Back-compat / no-regression: with no binary, the bare
        ``["cursor-agent", "--version"]`` list runs under shell=True, unchanged."""
        fake = MagicMock(returncode=0, stdout="1.2.3 (Cursor Agent)", stderr="")
        with patch(f"{_WIN_CURSOR_MOD}.subprocess.run", return_value=fake) as run:
            version = self.Detector().get_version()
        self.assertEqual(version, "1.2.3")
        self.assertEqual(run.call_args.args[0], ["cursor-agent", "--version"])
        self.assertIs(run.call_args.kwargs.get("shell"), True)

    def test_version_probe_failure_returns_none(self):
        """A failing/absent binary -> None (caller falls back to "Unknown"),
        never raises (headless MDM scan must not crash)."""
        with patch(f"{_WIN_CURSOR_MOD}.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(self.Detector().get_version(r"C:\x y\cursor-agent.exe"))


if __name__ == "__main__":
    unittest.main()
