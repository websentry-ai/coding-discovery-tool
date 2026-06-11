"""Binary-gate tests for GitHub Copilot CLI detection (macOS + Linux).

Copilot CLI detection moved from the ``~/.copilot`` config dir (also written by
the IDE Copilot agent and by Unbound's own MDM onboarding hook, and surviving a
CLI uninstall) to the ``copilot`` binary. These tests pin both directions plus
the new Linux detector:

  (a) ``~/.copilot`` with only ``hooks/unbound.json`` and NO binary -> None
      (kills BOTH the config-residue FP and the Unbound-hook self-FP at once)
  (b) ``~/.copilot`` + ``~/.local/bin/copilot`` (executable) -> detected,
      install_path is the binary
  (c) the new ``LinuxCopilotCliDetector`` detects via the same binary gate

The npm-global resolver is neutralised and ``is_running_as_root`` is pinned False
so the gate depends ONLY on the per-user on-disk binary the test creates under the
hermetic home (otherwise a CI box with a real ``copilot`` on PATH would leak in).
The Homebrew owner-attribution branch (which uses ``os.stat``) is covered by a
separate POSIX-only test that scopes its ``os.stat`` mock to the target path.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector
from scripts.coding_discovery_tools.linux.copilot_cli.copilot_cli import LinuxCopilotCliDetector

_MAC_MOD = "scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli"
_LINUX_MOD = "scripts.coding_discovery_tools.linux.copilot_cli.copilot_cli"


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho copilot\n", encoding="utf-8")
    os.chmod(path, 0o755)


def _make_hooks_only(user_home: Path) -> None:
    """The Unbound MDM-hook residue repro: ~/.copilot/hooks/unbound.json, no binary."""
    hooks = user_home / ".copilot" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "unbound.json").write_text('{"version": 1}', encoding="utf-8")


class _CopilotBinaryGateMixin:
    """Shared both-directions assertions. Subclasses set the detector class and
    the module path of its ``is_running_as_root`` / ``resolve_npm_global_tool_bin``
    seams."""

    Detector = None
    mod = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.detector = self.Detector()
        self.detector.user_home = self.home
        self._patchers = [
            patch(f"{self.mod}.is_running_as_root", return_value=False),
            patch(f"{self.mod}.resolve_npm_global_tool_bin", return_value=None),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self.tmp.cleanup()

    def test_hooks_only_without_binary_not_detected(self):
        """~/.copilot with only hooks/unbound.json (Unbound's MDM hook) and NO
        binary -> None. Kills both the config-residue FP and the hook self-FP."""
        _make_hooks_only(self.home)
        self.assertIsNone(self.detector.detect())

    @unittest.skipIf(os.name == "nt", "POSIX X_OK semantics for the ~/.local/bin stub")
    def test_binary_present_detected(self):
        """~/.copilot hooks residue PLUS an executable ~/.local/bin/copilot ->
        detected; install_path is the binary (the config dir never vetoes it)."""
        _make_hooks_only(self.home)
        binary = self.home / ".local" / "bin" / "copilot"
        _write_executable(binary)
        with patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "GitHub Copilot CLI")
        self.assertEqual(result["install_path"], str(binary))

    def test_nothing_present_not_detected(self):
        self.assertIsNone(self.detector.detect())


class TestMacOSCopilotCliBinaryGate(_CopilotBinaryGateMixin, unittest.TestCase):
    Detector = MacOSCopilotCliDetector
    mod = _MAC_MOD

    @unittest.skipIf(os.name == "nt", "Homebrew owner-attribution is a POSIX path")
    def test_homebrew_binary_owner_attributed_under_root(self):
        """Under a root scan, a machine-global ``/opt/homebrew/bin/copilot`` is
        attributed to the scanned user only when owned by them (or root). The
        ``os.stat`` mock is SCOPED to that path so pathlib's own stat calls
        elsewhere are untouched (gotcha #2); root is pinned True for this case."""
        brew = Path("/opt/homebrew/bin/copilot")
        real_stat = os.stat

        class _FakeStat:
            st_uid = 0  # root-owned -> attributes to every scanned user

        def scoped_stat(path, *a, **k):
            if str(path) == str(brew):
                return _FakeStat()
            return real_stat(path, *a, **k)

        # No per-user binary on disk, so the resolver falls through to the
        # machine-global Homebrew candidate. exists()/access() for that path are
        # forced True; root pinned True so the owner-attribution branch runs.
        def fake_exists(self):
            return str(self) == str(brew)

        with patch(f"{_MAC_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MAC_MOD}.resolve_npm_global_tool_bin", return_value=None), \
             patch.object(Path, "exists", fake_exists), \
             patch("os.access", lambda p, m: str(p) == str(brew)), \
             patch("os.stat", scoped_stat), \
             patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(brew))


class TestLinuxCopilotCliBinaryGate(_CopilotBinaryGateMixin, unittest.TestCase):
    Detector = LinuxCopilotCliDetector
    mod = _LINUX_MOD

    @unittest.skipIf(os.name == "nt", "POSIX X_OK semantics for the ~/.local/bin stub")
    def test_linux_detector_detects_via_binary(self):
        """The new Linux detector detects through the inherited binary gate."""
        binary = self.home / ".local" / "bin" / "copilot"
        _write_executable(binary)
        with patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    @unittest.skipIf(os.name == "nt", "POSIX path; /usr/local/bin is a Linux machine-global")
    def test_linux_usr_local_bin_detected(self):
        """Linux resolves the machine-global ``/usr/local/bin/copilot`` (no
        Homebrew on Linux). The ``exists``/``access`` checks for that path are
        forced; no per-user binary on disk so the resolver falls through."""
        target = Path("/usr/local/bin/copilot")

        def fake_exists(self):
            return str(self) == str(target)

        with patch.object(Path, "exists", fake_exists), \
             patch("os.access", lambda p, m: str(p) == str(target)), \
             patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(target))


if __name__ == "__main__":
    unittest.main()
