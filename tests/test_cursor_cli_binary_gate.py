"""Binary-gate tests for Cursor CLI detection.

Cursor CLI (``cursor-agent``) detection moved from the ``~/.cursor/cli-config.json``
residue (also written by the Cursor IDE, and surviving a CLI uninstall) to the
``cursor-agent`` binary. These tests drive the live per-user entry point
``detect_tool_for_user`` and pin both directions:

  (a) ``cli-config.json`` only, no binary -> None (the residue FP this fix kills)
  (b) ``~/.local/bin/cursor-agent`` (executable) -> detected, install_path is the binary
  (c) only the IDE ``cursor`` launcher on PATH -> None (the mis-detect guard:
      gating on ``cursor-agent`` must not pick up the IDE ``cursor`` binary)

The npm-global resolver and the ``which`` PATH backstop are neutralised, and
``is_running_as_root`` is pinned False, so the gate depends ONLY on the per-user
on-disk binary the test creates under the hermetic home (otherwise a CI box with
a real ``cursor-agent`` on PATH would leak in).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.user_tool_detector as utd
from scripts.coding_discovery_tools.macos.cursor_cli.cursor_cli import MacOSCursorCliDetector

_MOD = "scripts.coding_discovery_tools.user_tool_detector"


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

    The native Windows installer (``irm 'https://cursor.com/install?win32=true'
    | iex``) drops ``cursor-agent``/``agent`` (``.exe``/``.cmd``) at the root of
    ``%LOCALAPPDATA%\\cursor-agent`` and keeps the real binary under a
    ``versions\\<v>\\`` subdir — none of which the old single-candidate Windows
    branch (``.local\\bin\\cursor-agent.exe`` only) probed, so every native
    install was a false negative. The Git-Bash variant drops an extensionless
    ``~/.local/bin/cursor-agent``.

    The Windows branch is EXISTENCE-gated (on Windows ``os.access(X_OK)`` is True
    for any file), so these tests create plain files and never chmod — no
    ``skipIf(os.name == 'nt')`` is needed. ``platform.system`` is pinned to
    ``"Windows"`` so the Windows branch runs on a POSIX CI box, and
    ``is_running_as_root`` is irrelevant to this branch (no machine-global probe).
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
        -> detected. Fails against the old single-candidate Windows branch."""
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


if __name__ == "__main__":
    unittest.main()
