"""Windows machine-wide install locations that a per-user candidate list misses.

Device 79MZ494 reported zero tools while holding `C:\\Program Files\\ClaudeCode`,
`C:\\Program Files\\GitHubCopilot` and `%LOCALAPPDATA%\\Programs\\GitHub Copilot`.
Every Windows candidate was home-relative, so nothing outside a user profile was
ever probed — even though the managed rules, settings and MCP extractors already
name the ClaudeCode root.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import find_claude_binary_for_user
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli import WindowsCopilotCliDetector
from scripts.coding_discovery_tools.windows.github_copilot_app import WindowsGitHubCopilotAppDetector


class WindowsProgramFilesRootsTests(unittest.TestCase):
    def test_roots_resolve_from_the_environment(self):
        env = {"ProgramW6432": r"D:\Program Files", "ProgramFiles(x86)": r"D:\Program Files (x86)"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                utils_mod.windows_program_files_roots(),
                [Path(r"D:\Program Files"), Path(r"D:\Program Files (x86)")],
            )

    def test_duplicate_roots_are_collapsed(self):
        env = {"ProgramW6432": r"C:\Program Files", "ProgramFiles": r"C:\Program Files"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(utils_mod.windows_program_files_roots(), [Path(r"C:\Program Files")])

    def test_no_roots_without_the_variables(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(utils_mod.windows_program_files_roots(), [])


class ClaudeEnterpriseInstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "Users" / "ShobanaK"
        self.home.mkdir(parents=True)
        self.program_files = root / "Program Files"
        self.program_files.mkdir()
        self.addCleanup(self._tmp.cleanup)
        p = patch("platform.system", return_value="Windows")
        p.start()
        self.addCleanup(p.stop)

    def _install(self, name="claude.exe"):
        install_dir = self.program_files / "ClaudeCode"
        install_dir.mkdir()
        binary = install_dir / name
        binary.write_text("")
        os.chmod(binary, 0o755)
        return binary

    def test_enterprise_install_is_found(self):
        binary = self._install()
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            self.assertEqual(find_claude_binary_for_user(self.home), str(binary))

    def test_absent_install_still_reports_nothing(self):
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            self.assertIsNone(find_claude_binary_for_user(self.home))

    def test_user_install_wins_over_the_machine_wide_one(self):
        self._install()
        own = self.home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe"
        own.parent.mkdir(parents=True)
        own.write_text("")
        os.chmod(own, 0o755)
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            self.assertEqual(find_claude_binary_for_user(self.home), str(own))


class GitHubCopilotAppTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "Users" / "ShobanaK"
        self.home.mkdir(parents=True)
        self.program_files = root / "Program Files"
        self.program_files.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _detector(self):
        detector = WindowsGitHubCopilotAppDetector()
        detector.user_home = self.home
        return detector

    def test_machine_wide_install_is_detected(self):
        install = self.program_files / "GitHubCopilot"
        install.mkdir()
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            result = self._detector().detect()
        self.assertEqual(result["name"], "GitHub Copilot App")
        self.assertEqual(result["install_path"], str(install))

    def test_per_user_install_is_detected(self):
        install = self.home / "AppData" / "Local" / "Programs" / "GitHub Copilot"
        install.mkdir(parents=True)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self._detector().detect()["install_path"], str(install))

    def test_absent_install_reports_nothing(self):
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            self.assertIsNone(self._detector().detect())

    def test_nothing_inside_the_directory_is_executed(self):
        install = self.program_files / "GitHubCopilot"
        install.mkdir()
        (install / "copilot-desktop.exe").write_text("")
        with patch.object(utils_mod, "run_command") as run:
            with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
                result = self._detector().detect()
        self.assertIsNotNone(result)
        self.assertIsNone(result["version"])
        run.assert_not_called()

    def test_the_cli_detector_does_not_claim_the_app(self):
        """The app dir holds no `copilot.exe`, and the CLI resolver must not
        reach into it regardless."""
        install = self.program_files / "GitHubCopilot"
        install.mkdir()
        (install / "copilot-desktop.exe").write_text("")
        with patch.dict(os.environ, {"ProgramW6432": str(self.program_files)}, clear=True):
            self.assertIsNone(WindowsCopilotCliDetector._resolve_windows_binary(self.home))

    def test_registered_on_windows_only(self):
        self.assertIsInstance(
            ToolDetectorFactory.create_copilot_app_detector("Windows"),
            WindowsGitHubCopilotAppDetector,
        )
        self.assertIsNone(ToolDetectorFactory.create_copilot_app_detector("Darwin"))
        self.assertIsNone(ToolDetectorFactory.create_copilot_app_detector("Linux"))


if __name__ == "__main__":
    unittest.main()
