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
from scripts.coding_discovery_tools.utils import (
    get_claude_subscription_type,
    _get_plan_from_keychain,
)


class TestGetPlanFromKeychain(unittest.TestCase):
    """Tests for _get_plan_from_keychain direct Keychain reader."""

    def _mock_result(self, stdout="", returncode=0):
        mock = MagicMock(spec=subprocess.CompletedProcess)
        mock.stdout = stdout
        mock.returncode = returncode
        return mock

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_plan_from_keychain(self, _mock_root, mock_run):
        """Extracts subscriptionType from keychain credentials JSON."""
        creds = {"claudeAiOauth": {"subscriptionType": "max", "accessToken": "sk-..."}}
        mock_run.return_value = self._mock_result(stdout=json.dumps(creds))
        self.assertEqual(_get_plan_from_keychain("testuser"), "max")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_when_no_entry(self, _mock_root, mock_run):
        """Returns None when keychain entry does not exist."""
        mock_run.return_value = self._mock_result(returncode=44)
        self.assertIsNone(_get_plan_from_keychain("unknown"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_invalid_json(self, _mock_root, mock_run):
        """Returns None when keychain value is not valid JSON."""
        mock_run.return_value = self._mock_result(stdout="not json")
        self.assertIsNone(_get_plan_from_keychain("testuser"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_when_no_oauth_key(self, _mock_root, mock_run):
        """Returns None when JSON lacks claudeAiOauth key."""
        mock_run.return_value = self._mock_result(stdout=json.dumps({"other": "data"}))
        self.assertIsNone(_get_plan_from_keychain("testuser"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_timeout(self, _mock_root, mock_run):
        """Returns None when security command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="security", timeout=5)
        self.assertIsNone(_get_plan_from_keychain("testuser"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_returns_none_on_os_error(self, _mock_root, mock_run):
        """Returns None when security binary is missing."""
        mock_run.side_effect = OSError("No such file")
        self.assertIsNone(_get_plan_from_keychain("testuser"))

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_appends_keychain_path_when_root(self, _mock_root, mock_run):
        """When running as root, passes explicit keychain path."""
        creds = {"claudeAiOauth": {"subscriptionType": "pro"}}
        mock_run.return_value = self._mock_result(stdout=json.dumps(creds))
        _get_plan_from_keychain("alice")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[-1], "/Users/alice/Library/Keychains/login.keychain-db")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_does_not_append_keychain_path_when_not_root(self, _mock_root, mock_run):
        """When not root, does not pass explicit keychain path."""
        creds = {"claudeAiOauth": {"subscriptionType": "max"}}
        mock_run.return_value = self._mock_result(stdout=json.dumps(creds))
        _get_plan_from_keychain("alice")
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("/Users/alice/Library/Keychains/login.keychain-db", cmd)
        self.assertEqual(cmd[-1], "-w")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_keychain_fast_path_skips_cli(self, _mock_root, mock_run):
        """When keychain succeeds, get_claude_subscription_type returns immediately without CLI."""
        creds = {"claudeAiOauth": {"subscriptionType": "team"}}
        mock_run.return_value = self._mock_result(stdout=json.dumps(creds))
        with patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin"):
            result = get_claude_subscription_type("testuser", "/usr/local/bin/claude")
        self.assertEqual(result, "team")
        # Only one subprocess call (keychain), not two (keychain + CLI)
        self.assertEqual(mock_run.call_count, 1)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "security")

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_keychain_failure_falls_through_to_cli(self, _mock_root, mock_run):
        """When keychain fails, falls through to CLI approach."""
        mock_run.side_effect = [
            self._mock_result(returncode=44),  # keychain: no entry
            self._mock_result(stdout=json.dumps({"subscriptionType": "pro"})),  # CLI: success
        ]
        with patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin"):
            result = get_claude_subscription_type("testuser", "/usr/local/bin/claude")
        self.assertEqual(result, "pro")
        self.assertEqual(mock_run.call_count, 2)


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
            self.assertIn(os.path.join(".nvm", "versions", "node"), result)

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
    """Tests for get_claude_subscription_type CLI fallback parsing.

    The keychain fast-path is patched to return None so every test
    exercises the CLI-based fallback path.
    """

    def setUp(self):
        self.claude_binary = "/usr/local/bin/claude"
        self.username = "testuser"
        patcher = patch(
            "scripts.coding_discovery_tools.utils._get_plan_from_keychain",
            return_value=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mock_result(self, stdout="", returncode=0, stderr=""):
        """Create a mock subprocess.CompletedProcess."""
        mock = MagicMock(spec=subprocess.CompletedProcess)
        mock.stdout = stdout
        mock.returncode = returncode
        mock.stderr = stderr
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

    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/zsh")
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_uses_launchctl_asuser_when_root_on_macos(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_shell
    ):
        """On macOS as root, command uses 'launchctl asuser <uid>' with user shell."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "max"})
        )
        get_claude_subscription_type(self.username, self.claude_binary)
        args = mock_run.call_args_list[0][0][0]
        self.assertEqual(args[0], "launchctl")
        self.assertEqual(args[1], "asuser")
        self.assertEqual(args[2], "501")
        self.assertEqual(args[3], "/bin/zsh")
        self.assertEqual(args[4], "-lc")
        self.assertIn("auth status --json", args[5])

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

    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/bash")
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_falls_back_to_su_when_launchctl_fails(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_shell
    ):
        """On macOS as root, falls back to 'su -' when launchctl asuser fails."""
        mock_run.side_effect = [
            self._mock_result(stdout="", returncode=1, stderr="service error"),
            self._mock_result(
                stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
            ),
        ]
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "pro")
        first_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(first_args[0], "launchctl")
        second_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(second_args[0], "su")

    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=None)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_skips_launchctl_when_uid_unknown(
        self, _mock_root, _mock_sys, mock_run, _mock_uid
    ):
        """Skips launchctl asuser when UID cannot be resolved, uses su directly."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "team"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "team")
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "su")

    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/bash")
    @patch("scripts.coding_discovery_tools.utils._get_real_home", return_value="/Users/testuser")
    @patch("scripts.coding_discovery_tools.utils._is_daemon_container", return_value=True)
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_uses_launchctl_in_daemon_container(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_container,
        _mock_home, _mock_shell
    ):
        """In daemon container (non-root), uses launchctl asuser then direct."""
        mock_run.side_effect = [
            self._mock_result(stdout="", returncode=1, stderr="error"),
            self._mock_result(
                stdout=json.dumps({"loggedIn": True, "subscriptionType": "max"})
            ),
        ]
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "max")
        first_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(first_args[0], "launchctl")
        second_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(second_args[0], self.claude_binary)

    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/bash")
    @patch("scripts.coding_discovery_tools.utils._get_real_home", return_value="/Users/testuser")
    @patch("scripts.coding_discovery_tools.utils._is_daemon_container", return_value=True)
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_daemon_container_overrides_home_on_direct_exec(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_container,
        _mock_home, _mock_shell
    ):
        """In daemon container, direct execution overrides HOME to real user home."""
        mock_run.side_effect = [
            self._mock_result(stdout="", returncode=1, stderr="error"),
            self._mock_result(
                stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
            ),
        ]
        get_claude_subscription_type(self.username, self.claude_binary)
        # Second call (direct exec) should have env with HOME override
        kwargs = mock_run.call_args_list[1][1]
        self.assertEqual(kwargs["env"]["HOME"], "/Users/testuser")

    @patch("scripts.coding_discovery_tools.utils._is_daemon_container", return_value=False)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=False)
    def test_runs_directly_when_not_root_no_container(
        self, _mock_root, mock_run, _mock_container
    ):
        """When not root and not in daemon container, runs directly."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
        )
        get_claude_subscription_type(self.username, self.claude_binary)
        args = mock_run.call_args[0][0]
        self.assertEqual(args, [self.claude_binary, "auth", "status", "--json"])

    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Linux")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_runs_directly_on_linux_as_root(self, _mock_root, _mock_sys, mock_run):
        """On Linux as root, command runs directly (no launchctl or su)."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": True, "subscriptionType": "enterprise"})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "enterprise")
        args = mock_run.call_args[0][0]
        self.assertEqual(args, [self.claude_binary, "auth", "status", "--json"])

    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/bash")
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_not_logged_in_short_circuits_fallback(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_shell
    ):
        """When launchctl succeeds but user is not logged in, does not try su."""
        mock_run.return_value = self._mock_result(
            stdout=json.dumps({"loggedIn": False, "subscriptionType": None})
        )
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertIsNone(result)
        # Only one subprocess call (launchctl), no su fallback
        self.assertEqual(mock_run.call_count, 1)


    @patch("scripts.coding_discovery_tools.utils._get_compatible_shell", return_value="/bin/bash")
    @patch("scripts.coding_discovery_tools.utils._get_real_home", return_value="/Users/testuser")
    @patch("scripts.coding_discovery_tools.utils._is_daemon_container", return_value=True)
    @patch("scripts.coding_discovery_tools.utils._get_uid_for_user", return_value=501)
    @patch("scripts.coding_discovery_tools.utils.subprocess.run")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils._is_root", return_value=True)
    def test_root_darwin_container_falls_through_to_direct_exec(
        self, _mock_root, _mock_sys, mock_run, _mock_uid, _mock_container,
        _mock_home, _mock_shell
    ):
        """When root on Darwin in daemon container, falls through to direct exec if launchctl and su fail."""
        mock_run.side_effect = [
            self._mock_result(stdout="", returncode=1, stderr="launchctl error"),
            self._mock_result(stdout="", returncode=1, stderr="su error"),
            self._mock_result(
                stdout=json.dumps({"loggedIn": True, "subscriptionType": "pro"})
            ),
        ]
        result = get_claude_subscription_type(self.username, self.claude_binary)
        self.assertEqual(result, "pro")
        self.assertEqual(mock_run.call_count, 3)
        # Third call is direct execution with HOME override
        kwargs = mock_run.call_args_list[2][1]
        self.assertEqual(kwargs["env"]["HOME"], "/Users/testuser")


class TestHelpers(unittest.TestCase):
    """Tests for helper functions used in subscription detection."""

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_uid_for_user_success(self, mock_pwd):
        """Returns UID when user is found."""
        from scripts.coding_discovery_tools.utils import _get_uid_for_user
        mock_pwd.getpwnam.return_value = MagicMock(pw_uid=501)
        self.assertEqual(_get_uid_for_user("testuser"), 501)

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_uid_for_user_not_found(self, mock_pwd):
        """Returns None when user is not found."""
        from scripts.coding_discovery_tools.utils import _get_uid_for_user
        mock_pwd.getpwnam.side_effect = KeyError("user not found")
        self.assertIsNone(_get_uid_for_user("unknown"))

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_real_home_success(self, mock_pwd):
        """Returns home directory when user is found."""
        from scripts.coding_discovery_tools.utils import _get_real_home
        mock_pwd.getpwnam.return_value = MagicMock(pw_dir="/Users/testuser")
        self.assertEqual(_get_real_home("testuser"), "/Users/testuser")

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_real_home_not_found(self, mock_pwd):
        """Returns None when user is not found."""
        from scripts.coding_discovery_tools.utils import _get_real_home
        mock_pwd.getpwnam.side_effect = KeyError("user not found")
        self.assertIsNone(_get_real_home("unknown"))

    @patch("scripts.coding_discovery_tools.utils.Path.home")
    def test_is_daemon_container_true(self, mock_home):
        """Detects Daemon Container path."""
        from scripts.coding_discovery_tools.utils import _is_daemon_container
        mock_home.return_value = Path(
            "/Users/pugazh/Library/Daemon Containers/ABC123/Data/Downloads"
        )
        self.assertTrue(_is_daemon_container())

    @patch("scripts.coding_discovery_tools.utils.Path.home")
    def test_is_daemon_container_false(self, mock_home):
        """Returns False for normal home directory."""
        from scripts.coding_discovery_tools.utils import _is_daemon_container
        mock_home.return_value = Path("/Users/pugazh")
        self.assertFalse(_is_daemon_container())

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_compatible_shell_zsh(self, mock_pwd):
        """Returns /bin/zsh for a user whose login shell is zsh."""
        from scripts.coding_discovery_tools.utils import _get_compatible_shell
        mock_pwd.getpwnam.return_value = MagicMock(pw_shell="/bin/zsh")
        self.assertEqual(_get_compatible_shell("zshuser"), "/bin/zsh")

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_compatible_shell_bash(self, mock_pwd):
        """Returns /bin/bash for a user whose login shell is bash."""
        from scripts.coding_discovery_tools.utils import _get_compatible_shell
        mock_pwd.getpwnam.return_value = MagicMock(pw_shell="/bin/bash")
        self.assertEqual(_get_compatible_shell("bashuser"), "/bin/bash")

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_compatible_shell_fish_falls_back(self, mock_pwd):
        """Falls back to /bin/bash when user shell is fish (not in allowlist)."""
        from scripts.coding_discovery_tools.utils import _get_compatible_shell
        mock_pwd.getpwnam.return_value = MagicMock(pw_shell="/usr/local/bin/fish")
        self.assertEqual(_get_compatible_shell("fishuser"), "/bin/bash")

    @patch("scripts.coding_discovery_tools.utils.pwd")
    def test_get_compatible_shell_lookup_fails(self, mock_pwd):
        """Falls back to /bin/bash when user lookup raises KeyError."""
        from scripts.coding_discovery_tools.utils import _get_compatible_shell
        mock_pwd.getpwnam.side_effect = KeyError("user not found")
        self.assertEqual(_get_compatible_shell("unknown"), "/bin/bash")


if __name__ == "__main__":
    unittest.main()
