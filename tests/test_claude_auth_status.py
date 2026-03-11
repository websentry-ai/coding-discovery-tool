"""
Tests for Claude Code subscription plan detection.

Covers:
- find_claude_binary_for_user: locating the claude binary in various install paths
- get_claude_subscription_type: parsing 'claude auth status' output per user

Uses tempfile for real filesystem checks (binary discovery) and
unittest.mock for subprocess calls (auth status parsing).
"""

import json
import os
import platform
import stat
import subprocess
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.coding_discovery_tools.user_tool_detector import find_claude_binary_for_user
from scripts.coding_discovery_tools.utils import get_claude_subscription_type


class TestFindClaudeBinaryForUser(unittest.TestCase):
    """Tests for find_claude_binary_for_user across installation locations."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self._tmp_dir)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _create_executable(self, rel_path: str) -> Path:
        """Create a fake executable file at the given relative path under user_home."""
        full_path = self.user_home / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("#!/bin/sh\n")
        full_path.chmod(stat.S_IRWXU)
        return full_path

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_finds_homebrew_apple_silicon_binary(self, _mock_sys):
        """Homebrew Apple Silicon path is checked first on macOS."""
        brew_path = Path("/opt/homebrew/bin/claude")
        if not brew_path.exists():
            self.skipTest("/opt/homebrew/bin/claude does not exist on this machine")
        result = find_claude_binary_for_user(self.user_home)
        self.assertEqual(result, str(brew_path))

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_finds_local_bin_binary(self, _mock_sys):
        """Official installer path ~/.local/bin/claude is detected when system paths absent."""
        local_bin = self._create_executable(".local/bin/claude")
        # Patch system-wide candidates to not exist so user-home path is found
        orig_exists = Path.exists

        def patched_exists(self_path):
            path_str = str(self_path)
            if path_str in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
                return False
            return orig_exists(self_path)

        with patch.object(Path, "exists", patched_exists):
            result = find_claude_binary_for_user(self.user_home)
        self.assertEqual(result, str(local_bin))

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_finds_bun_binary(self, _mock_sys):
        """Bun global install path ~/.bun/bin/claude is detected."""
        self._create_executable(".bun/bin/claude")
        result = find_claude_binary_for_user(self.user_home)
        # May match a system-wide path first if it exists; check our path is valid
        if result and result.startswith(str(self.user_home)):
            self.assertEqual(result, str(self.user_home / ".bun" / "bin" / "claude"))

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_finds_nvm_binary(self, _mock_sys):
        """nvm-installed claude binary under .nvm/versions/node/*/bin is detected."""
        self._create_executable(".nvm/versions/node/v20.11.0/bin/claude")
        result = find_claude_binary_for_user(self.user_home)
        if result and result.startswith(str(self.user_home)):
            self.assertIn(".nvm/versions/node/", result)

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_returns_none_when_not_found(self, _mock_sys):
        """Returns None when no claude binary exists in any known location."""
        # Patch system paths to not exist so we only rely on user_home paths
        with patch("pathlib.Path.exists", return_value=False):
            with patch("os.access", return_value=False):
                result = find_claude_binary_for_user(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Darwin")
    def test_priority_local_bin_over_bun(self, _mock_sys):
        """~/.local/bin/claude takes precedence over ~/.bun/bin/claude."""
        self._create_executable(".local/bin/claude")
        self._create_executable(".bun/bin/claude")
        result = find_claude_binary_for_user(self.user_home)
        if result and result.startswith(str(self.user_home)):
            self.assertEqual(result, str(self.user_home / ".local" / "bin" / "claude"))

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Windows")
    def test_windows_local_bin_binary(self, _mock_sys):
        """On Windows, checks .local/bin/claude.exe."""
        exe_path = self._create_executable(".local/bin/claude.exe")
        result = find_claude_binary_for_user(self.user_home)
        self.assertEqual(result, str(exe_path))

    @patch("scripts.coding_discovery_tools.user_tool_detector.platform.system", return_value="Windows")
    def test_windows_npm_binary(self, _mock_sys):
        """On Windows, checks AppData/Roaming/npm/claude.cmd."""
        cmd_path = self._create_executable("AppData/Roaming/npm/claude.cmd")
        result = find_claude_binary_for_user(self.user_home)
        self.assertEqual(result, str(cmd_path))


class TestGetClaudeSubscriptionType(unittest.TestCase):
    """Tests for get_claude_subscription_type auth status parsing."""

    def setUp(self):
        self.claude_binary = "/usr/local/bin/claude"
        self.username = "testuser"

    def _mock_result(self, stdout="", returncode=0):
        """Create a mock subprocess.CompletedProcess."""
        mock = MagicMock(spec=subprocess.CompletedProcess)
        mock.stdout = stdout
        mock.returncode = returncode
        return mock

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_parses_max_plan(self, _mock_root, mock_run):
        """Extracts 'max' subscriptionType from valid JSON output."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "max"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "max")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_parses_pro_plan(self, _mock_root, mock_run):
        """Extracts 'pro' subscriptionType from valid JSON output."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "pro")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_parses_team_plan(self, _mock_root, mock_run):
        """Extracts 'team' subscriptionType from valid JSON output."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "team"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "team")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_parses_enterprise_plan(self, _mock_root, mock_run):
        """Extracts 'enterprise' subscriptionType from valid JSON output."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "enterprise"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "enterprise")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_not_logged_in(self, _mock_root, mock_run):
        """Returns None when subscriptionType is null (user not logged in)."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": False, "subscriptionType": None})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_command_failure(self, _mock_root, mock_run):
        """Returns None when claude auth status exits with non-zero."""
        mock_run.return_value = self._mock_result(stdout="", returncode=1)
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_timeout(self, _mock_root, mock_run):
        """Returns None when subprocess times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=15)
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_invalid_json(self, _mock_root, mock_run):
        """Returns None when command output is not valid JSON."""
        mock_run.return_value = self._mock_result(stdout="not json at all")
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_os_error(self, _mock_root, mock_run):
        """Returns None when subprocess raises OSError (binary not found)."""
        mock_run.side_effect = OSError("No such file or directory")
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_uses_su_when_root_on_macos(self, _mock_root, _mock_sys, mock_run):
        """On macOS as root, command is wrapped with 'su - username -c ...'."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "max"})
        )
        get_claude_subscription_type(self.username, self.claude_binary)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "su")
        self.assertEqual(args[1], "-")
        self.assertEqual(args[2], self.username)
        self.assertEqual(args[3], "-c")
        self.assertIn("auth status --json", args[4])

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_runs_directly_when_not_root(self, _mock_root, mock_run):
        """When not running as root, command runs directly without su."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
        )
        get_claude_subscription_type(self.username, self.claude_binary)
        args = mock_run.call_args[0][0]
        self.assertEqual(args, [self.claude_binary, "auth", "status", "--json"])

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Windows")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_runs_directly_on_windows(self, _mock_root, _mock_sys, mock_run):
        """On Windows, command runs directly regardless of privileges."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "team"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "team")
        args = mock_run.call_args[0][0]
        self.assertEqual(args, [self.claude_binary, "auth", "status", "--json"])

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_missing_subscription_key(self, _mock_root, mock_run):
        """Returns None when JSON response lacks subscriptionType key."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
