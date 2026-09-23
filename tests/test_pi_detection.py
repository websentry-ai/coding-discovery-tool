"""Detection tests for the pi coding agent (macOS + Linux).

``pi`` is a two-letter binary name shared with unrelated tools (the Raspberry Pi
helpers, ``pi`` pretty-printers, personal scripts), so a bare
``which pi``/``~/.local/bin/pi`` hit is NOT proof of install. These tests pin
both directions of the corroboration gate: a real pi-coding-agent layout is
detected, and a look-alike ``pi`` binary with no agent evidence is not.

Every test drives real temp dirs (no mocks of the filesystem) and runs on Linux
CI as well as macOS — the detector's paths are ``~``-relative on both.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.pi.pi import LinuxPiDetector
from scripts.coding_discovery_tools.macos.pi.pi import MacOSPiDetector

# The Linux detector subclasses the macOS one, so the version probe and binary
# resolution both live in (and are patched on) the macOS module.
_PI_MOD = "scripts.coding_discovery_tools.macos.pi.pi"


def _make_exec(path: Path) -> Path:
    """Create an executable stub file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class _PiDetectionMixin:
    """Shared detection assertions; subclasses set ``Detector``."""

    Detector = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.detector = self.Detector()
        self.detector.user_home = self.home

    def tearDown(self):
        self.tmp.cleanup()

    def _agent_dir(self) -> Path:
        agent = self.home / ".pi" / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        return agent

    # --- positive detection ----------------------------------------------

    def test_local_bin_binary_with_agent_dir_detected(self):
        binary = _make_exec(self.home / ".local" / "bin" / "pi")
        self._agent_dir()
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Pi Coding Agent")
        self.assertEqual(result["install_path"], str(binary))
        self.assertEqual(result["version"], "0.74.0")

    def test_managed_install_launcher_detected(self):
        agent = self._agent_dir()
        (agent / "install").mkdir(parents=True, exist_ok=True)
        (agent / "install" / "managed-install.json").write_text("{}", encoding="utf-8")
        binary = _make_exec(agent / "bin" / "pi")
        with patch(f"{_PI_MOD}.run_command", return_value="pi 1.2.3"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_managed_install_marker_alone_corroborates(self):
        """``managed-install.json`` present but no ``~/.pi/agent`` *contents*
        beyond it still corroborates a binary found elsewhere."""
        agent = self.home / ".pi" / "agent" / "install"
        agent.mkdir(parents=True)
        (agent / "managed-install.json").write_text("{}", encoding="utf-8")
        _make_exec(self.home / ".local" / "bin" / "pi")
        with patch(f"{_PI_MOD}.run_command", return_value="pi 1.0.0"):
            self.assertIsNotNone(self.detector.detect())

    def test_symlink_to_pi_coding_agent_package_detected(self):
        """No ``~/.pi/agent`` at all, but the binary resolves into a
        ``pi-coding-agent`` package dir -> that is the agent."""
        real = _make_exec(self.home / "pkgs" / "pi-coding-agent" / "bin" / "pi")
        link = self.home / ".local" / "bin" / "pi"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(real), str(link))
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.9.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(link))

    def test_homebrew_binary_with_agent_dir_detected(self):
        """Homebrew / npm -g installs live outside the home; a root/MDM scan must
        still find them (with ~/.pi/agent corroborating) without touching PATH."""
        brew = _make_exec(self.home / "opt" / "homebrew" / "bin" / "pi")
        self._agent_dir()
        with patch.object(self.detector, "MACHINE_GLOBAL_BIN_PATHS", [brew]), \
             patch(f"{_PI_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_PI_MOD}.run_command", return_value="pi 0.86.1"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(brew))

    def test_homebrew_binary_owned_by_other_user_skipped_when_root(self):
        brew = _make_exec(self.home / "opt" / "homebrew" / "bin" / "pi")
        self._agent_dir()
        with patch.object(self.detector, "MACHINE_GLOBAL_BIN_PATHS", [brew]), \
             patch(f"{_PI_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_PI_MOD}.machine_global_binary_owned_by_user", return_value=False), \
             patch(f"{_PI_MOD}._is_scanning_users_own_home", return_value=False), \
             patch(f"{_PI_MOD}.run_command", return_value="pi 0.86.1"):
            self.assertIsNone(self.detector.detect())

    def test_homebrew_binary_without_agent_dir_not_detected(self):
        """Machine-global paths still go through the collision guard."""
        brew = _make_exec(self.home / "opt" / "homebrew" / "bin" / "pi")
        with patch.object(self.detector, "MACHINE_GLOBAL_BIN_PATHS", [brew]), \
             patch(f"{_PI_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_PI_MOD}.run_command", return_value="pi 3.14"):
            self.assertIsNone(self.detector.detect())

    def test_agent_dir_override_honoured_for_own_home(self):
        override = self.home / "custom-agent"
        binary = _make_exec(override / "bin" / "pi")
        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(override)}), \
             patch(f"{_PI_MOD}._is_scanning_users_own_home", return_value=True), \
             patch(f"{_PI_MOD}.run_command", return_value="pi 0.86.1"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))

    def test_agent_dir_override_ignored_for_other_users_home(self):
        """Root/MDM scan: the scanner's own PI_CODING_AGENT_DIR must not
        corroborate or supply binaries for every scanned account."""
        override = self.home / "scanner-agent"
        _make_exec(override / "bin" / "pi")
        # Scanned user has an unrelated `pi` and NO ~/.pi/agent.
        _make_exec(self.home / ".local" / "bin" / "pi")
        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(override)}), \
             patch(f"{_PI_MOD}._is_scanning_users_own_home", return_value=False), \
             patch.object(self.detector, "MACHINE_GLOBAL_BIN_PATHS", []), \
             patch(f"{_PI_MOD}.run_command", return_value="pi 3.14"):
            self.assertIsNone(self.detector.detect())

    # --- false-positive / residue kills -----------------------------------

    def test_unrelated_pi_binary_not_detected(self):
        """THE FP kill: an executable named ``pi`` with no agent evidence
        anywhere must not be reported as the pi coding agent."""
        _make_exec(self.home / ".local" / "bin" / "pi")
        with patch(f"{_PI_MOD}.run_command", return_value="pi 3.14"):
            self.assertIsNone(self.detector.detect())

    def test_agent_config_residue_without_binary_not_detected(self):
        """Config residue survives uninstall; it alone is not an install."""
        agent = self._agent_dir()
        (agent / "settings.json").write_text("{}", encoding="utf-8")
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0"):
            self.assertIsNone(self.detector.detect())

    def test_empty_home_not_detected(self):
        with patch(f"{_PI_MOD}.run_command", return_value=None):
            self.assertIsNone(self.detector.detect())

    # --- version ----------------------------------------------------------

    def test_version_extracted_from_probe_output(self):
        binary = _make_exec(self.home / ".local" / "bin" / "pi")
        self._agent_dir()
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0 (darwin-arm64)"):
            self.assertEqual(self.detector.get_version(binary), "0.74.0")

    def test_version_unknown_when_probe_returns_nothing(self):
        _make_exec(self.home / ".local" / "bin" / "pi")
        self._agent_dir()
        with patch(f"{_PI_MOD}.run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "Unknown")

    # --- resolution order --------------------------------------------------

    def test_agent_bin_wins_over_local_bin(self):
        agent = self._agent_dir()
        agent_binary = _make_exec(agent / "bin" / "pi")
        _make_exec(self.home / ".local" / "bin" / "pi")
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0"):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(agent_binary))

    def test_bun_bin_binary_detected(self):
        binary = _make_exec(self.home / ".bun" / "bin" / "pi")
        self._agent_dir()
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0"):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(binary))

    def test_nvm_binary_detected(self):
        binary = _make_exec(
            self.home / ".nvm" / "versions" / "node" / "v20.11.0" / "bin" / "pi"
        )
        self._agent_dir()
        with patch(f"{_PI_MOD}.run_command", return_value="pi 0.74.0"):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(binary))

    def test_tool_name_is_canonical(self):
        # The FE matches ``\bpi\b``; the bare string "pi" must never be the row name.
        self.assertEqual(self.detector.tool_name, "Pi Coding Agent")


class TestMacOSPiDetection(_PiDetectionMixin, unittest.TestCase):
    Detector = MacOSPiDetector


class TestLinuxPiDetection(_PiDetectionMixin, unittest.TestCase):
    Detector = LinuxPiDetector

    def test_linux_subclasses_macos_detector(self):
        self.assertTrue(issubclass(LinuxPiDetector, MacOSPiDetector))


class TestPiDetectorFactory(unittest.TestCase):
    """The factory must wire pi for Darwin/Linux and stay silent on Windows."""

    def test_factory_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        self.assertIsInstance(ToolDetectorFactory.create_pi_detector("Darwin"), MacOSPiDetector)
        self.assertIsInstance(ToolDetectorFactory.create_pi_detector("Linux"), LinuxPiDetector)
        self.assertIsNone(ToolDetectorFactory.create_pi_detector("Windows"))
        self.assertIsNone(ToolDetectorFactory.create_pi_detector("Plan9"))

    def test_included_in_all_tool_detectors(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        for os_name in ("Darwin", "Linux"):
            names = [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)]
            self.assertIn("Pi Coding Agent", names, f"missing for {os_name}")
        windows_names = [
            d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors("Windows")
        ]
        self.assertNotIn("Pi Coding Agent", windows_names)

    def test_rules_extractor_factory_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import PiRulesExtractorFactory

        self.assertIsNotNone(PiRulesExtractorFactory.create("Darwin"))
        self.assertIsNotNone(PiRulesExtractorFactory.create("Linux"))
        self.assertIsNone(PiRulesExtractorFactory.create("Windows"))


if __name__ == "__main__":
    unittest.main()
