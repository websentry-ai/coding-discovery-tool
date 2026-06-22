"""
Tests for macOS and Windows user filtering logic.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.coding_discovery_tools.utils import (
    DsclBatchData,
    _fetch_dscl_batch_data,
    _is_human_user_macos,
    _parse_dscl_list_output,
    _real_user_or_none,
    _strip_windows_domain,
    get_all_users_macos,
    get_all_users_windows,
    get_audit_user,
    get_user_info,
)

EMPTY_BATCH = DsclBatchData(uid_map={}, shell_map={}, hidden_set=frozenset())


class TestParseDsclListOutput(unittest.TestCase):

    def test_normal_multiline_output(self):
        output = "alice           501\nbob             502\ndaemon          1"
        self.assertEqual(_parse_dscl_list_output(output), {"alice": "501", "bob": "502", "daemon": "1"})

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(_parse_dscl_list_output(""), {})

    def test_none_input_returns_empty_dict(self):
        self.assertEqual(_parse_dscl_list_output(None), {})

    def test_malformed_lines_skipped(self):
        output = "alice  501\n\n   \nsingletoken\nbob  502"
        self.assertEqual(_parse_dscl_list_output(output), {"alice": "501", "bob": "502"})

    def test_single_entry(self):
        self.assertEqual(_parse_dscl_list_output("admin  500"), {"admin": "500"})


class TestFetchDsclBatchData(unittest.TestCase):

    @patch("scripts.coding_discovery_tools.utils.run_command")
    def test_all_queries_succeed(self, mock_cmd):
        mock_cmd.side_effect = [
            "alice  501\nbob  502\ndaemon  1",
            "alice  /bin/zsh\nbob  /bin/bash\ndaemon  /usr/bin/false",
            "ripplingadmin  1\n_amavisd  1",
        ]
        data = _fetch_dscl_batch_data()
        self.assertEqual(data.uid_map, {"alice": 501, "bob": 502, "daemon": 1})
        self.assertEqual(data.shell_map, {"alice": "/bin/zsh", "bob": "/bin/bash", "daemon": "/usr/bin/false"})
        self.assertEqual(data.hidden_set, frozenset({"ripplingadmin", "_amavisd"}))

    @patch("scripts.coding_discovery_tools.utils.run_command")
    def test_uniqueid_query_fails(self, mock_cmd):
        mock_cmd.side_effect = [None, "alice  /bin/zsh", "hidden  1"]
        data = _fetch_dscl_batch_data()
        self.assertEqual(data.uid_map, {})
        self.assertEqual(data.shell_map, {"alice": "/bin/zsh"})
        self.assertEqual(data.hidden_set, frozenset({"hidden"}))

    @patch("scripts.coding_discovery_tools.utils.run_command")
    def test_ishidden_query_fails(self, mock_cmd):
        mock_cmd.side_effect = ["alice  501", "alice  /bin/zsh", None]
        data = _fetch_dscl_batch_data()
        self.assertEqual(data.uid_map, {"alice": 501})
        self.assertEqual(data.hidden_set, frozenset())

    @patch("scripts.coding_discovery_tools.utils.run_command", return_value=None)
    def test_all_queries_fail(self, _mock_cmd):
        data = _fetch_dscl_batch_data()
        self.assertEqual(data, EMPTY_BATCH)

    @patch("scripts.coding_discovery_tools.utils.run_command")
    def test_non_numeric_uid_skipped(self, mock_cmd):
        mock_cmd.side_effect = ["alice  501\nbaduser  notanumber\nbob  502", "alice  /bin/zsh", None]
        data = _fetch_dscl_batch_data()
        self.assertEqual(data.uid_map, {"alice": 501, "bob": 502})
        self.assertNotIn("baduser", data.uid_map)


class TestIsHumanUserMacos(unittest.TestCase):

    def _batch(self, uid_map=None, shell_map=None, hidden_set=None):
        return DsclBatchData(
            uid_map=uid_map if uid_map is not None else {"alice": 501, "bob": 502},
            shell_map=shell_map if shell_map is not None else {"alice": "/bin/zsh", "bob": "/bin/bash"},
            hidden_set=hidden_set if hidden_set is not None else frozenset(),
        )

    def test_human_user_passes(self):
        self.assertTrue(_is_human_user_macos("alice", self._batch()))

    def test_not_in_uid_map(self):
        self.assertFalse(_is_human_user_macos("unknown", self._batch()))

    def test_uid_below_threshold(self):
        self.assertFalse(_is_human_user_macos("daemon", self._batch(uid_map={"daemon": 1})))

    def test_non_interactive_shell(self):
        batch = self._batch(uid_map={"svc": 501}, shell_map={"svc": "/usr/bin/false"})
        self.assertFalse(_is_human_user_macos("svc", batch))

    def test_hidden_user(self):
        batch = self._batch(
            uid_map={"ripplingadmin": 501},
            shell_map={"ripplingadmin": "/bin/zsh"},
            hidden_set=frozenset({"ripplingadmin"}),
        )
        self.assertFalse(_is_human_user_macos("ripplingadmin", batch))

    def test_all_batch_empty_passes_through(self):
        self.assertTrue(_is_human_user_macos("anyone", EMPTY_BATCH))


class TestGetAllUsersMacos(unittest.TestCase):

    def _make_dir_entry(self, name: str, is_dir: bool = True):
        entry = MagicMock(spec=Path)
        entry.name = name
        entry.is_dir.return_value = is_dir
        return entry

    @patch("scripts.coding_discovery_tools.utils._is_human_user_macos", return_value=True)
    @patch("scripts.coding_discovery_tools.utils._fetch_dscl_batch_data", return_value=EMPTY_BATCH)
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_filters_shared_via_constant(self, _mock_sys, _mock_batch, _mock_human):
        mock_entries = [self._make_dir_entry("alice"), self._make_dir_entry("Shared"), self._make_dir_entry("bob")]
        with patch("scripts.coding_discovery_tools.utils.Path") as MockPath:
            mock_users_dir = MagicMock()
            mock_users_dir.exists.return_value = True
            mock_users_dir.iterdir.return_value = mock_entries
            MockPath.return_value = mock_users_dir
            result = get_all_users_macos()
        self.assertIn("alice", result)
        self.assertIn("bob", result)
        self.assertNotIn("Shared", result)

    @patch("scripts.coding_discovery_tools.utils._is_human_user_macos")
    @patch("scripts.coding_discovery_tools.utils._fetch_dscl_batch_data", return_value=EMPTY_BATCH)
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_filters_non_human_users(self, _mock_sys, _mock_batch, mock_human):
        mock_human.side_effect = lambda name, batch_data: name != "ripplingadmin"
        mock_entries = [self._make_dir_entry("alice"), self._make_dir_entry("ripplingadmin")]
        with patch("scripts.coding_discovery_tools.utils.Path") as MockPath:
            mock_users_dir = MagicMock()
            mock_users_dir.exists.return_value = True
            mock_users_dir.iterdir.return_value = mock_entries
            MockPath.return_value = mock_users_dir
            result = get_all_users_macos()
        self.assertIn("alice", result)
        self.assertNotIn("ripplingadmin", result)

    @patch("scripts.coding_discovery_tools.utils._is_human_user_macos", return_value=True)
    @patch("scripts.coding_discovery_tools.utils._fetch_dscl_batch_data")
    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_batch_data_fetched_exactly_once(self, _mock_sys, mock_batch, _mock_human):
        mock_batch.return_value = EMPTY_BATCH
        mock_entries = [self._make_dir_entry("alice"), self._make_dir_entry("bob"), self._make_dir_entry("charlie")]
        with patch("scripts.coding_discovery_tools.utils.Path") as MockPath:
            mock_users_dir = MagicMock()
            mock_users_dir.exists.return_value = True
            mock_users_dir.iterdir.return_value = mock_entries
            MockPath.return_value = mock_users_dir
            get_all_users_macos()
        mock_batch.assert_called_once()


class TestGetAllUsersWindows(unittest.TestCase):

    def _make_dir_entry(self, name: str, is_dir: bool = True):
        entry = MagicMock(spec=Path)
        entry.name = name
        entry.is_dir.return_value = is_dir
        return entry

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_returns_empty_on_non_windows(self, _mock_sys):
        self.assertEqual(get_all_users_windows(), [])

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Windows")
    def test_filters_system_dirs_via_constant(self, _mock_sys):
        mock_entries = [
            self._make_dir_entry("alice"),
            self._make_dir_entry("Public"),
            self._make_dir_entry("Default"),
            self._make_dir_entry("Default User"),
            self._make_dir_entry("All Users"),
            self._make_dir_entry("TEMP"),
            self._make_dir_entry("bob"),
            self._make_dir_entry(".hidden"),
        ]
        with patch("scripts.coding_discovery_tools.utils.Path") as MockPath:
            mock_home = MagicMock()
            mock_home.anchor = "C:\\"
            mock_users_dir = MagicMock()
            mock_users_dir.exists.return_value = True
            mock_users_dir.iterdir.return_value = mock_entries
            MockPath.home.return_value = mock_home
            MockPath.return_value.__truediv__ = MagicMock(return_value=mock_users_dir)
            result = get_all_users_windows()
        self.assertIn("alice", result)
        self.assertIn("bob", result)
        for excluded in ("Public", "Default", "Default User", "All Users", "TEMP", ".hidden"):
            self.assertNotIn(excluded, result)


class TestRealUserOrNone(unittest.TestCase):
    """The audit helper: real human in -> trimmed name; junk/service -> None."""

    def test_real_human_passes_through(self):
        self.assertEqual(_real_user_or_none("alice"), "alice")

    def test_real_human_is_trimmed(self):
        self.assertEqual(_real_user_or_none("  alice  "), "alice")

    def test_root_rejected(self):
        self.assertIsNone(_real_user_or_none("root"))

    def test_root_case_insensitive(self):
        self.assertIsNone(_real_user_or_none("ROOT"))

    def test_macos_daemon_underscore_prefix_rejected(self):
        self.assertIsNone(_real_user_or_none("_windowserver"))

    def test_system_rejected(self):
        self.assertIsNone(_real_user_or_none("SYSTEM"))

    def test_windows_machine_account_trailing_dollar_rejected(self):
        self.assertIsNone(_real_user_or_none("WIN-ABC$"))

    def test_linux_service_account_rejected(self):
        self.assertIsNone(_real_user_or_none("www-data"))

    def test_other_linux_service_accounts_rejected(self):
        for name in ("postgres", "nobody", "daemon", "nginx", "mysql"):
            self.assertIsNone(_real_user_or_none(name), name)

    def test_unknown_literal_rejected(self):
        self.assertIsNone(_real_user_or_none("unknown"))

    def test_empty_string_rejected(self):
        self.assertIsNone(_real_user_or_none(""))

    def test_whitespace_only_rejected(self):
        self.assertIsNone(_real_user_or_none("   "))

    def test_none_input_rejected(self):
        self.assertIsNone(_real_user_or_none(None))

    def test_windows_builtin_service_identities_rejected(self):
        for name in (
            "NT AUTHORITY\\LOCAL SERVICE",
            "NT AUTHORITY\\NETWORK SERVICE",
            "NT AUTHORITY\\SYSTEM",
            "nt authority\\local service",  # case-insensitive domain
            "NT SERVICE\\MSSQLSERVER",  # any NT SERVICE principal
        ):
            self.assertIsNone(_real_user_or_none(name), name)

    def test_bare_windows_builtins_rejected(self):
        for name in ("Administrator", "LocalSystem", "LOCAL SERVICE", "Network Service"):
            self.assertIsNone(_real_user_or_none(name), name)

    def test_domain_qualified_human_is_stripped_self_contained(self):
        # Self-contained: strips DOMAIN\\ even if a caller skips get_user_info.
        self.assertEqual(_real_user_or_none("CORP\\alice"), "alice")


class TestGetAuditUser(unittest.TestCase):
    """get_audit_user() returns the real human OR None (never junk)."""

    @patch("scripts.coding_discovery_tools.utils.get_user_info", return_value="alice")
    def test_returns_real_human(self, _mock):
        self.assertEqual(get_audit_user(), "alice")

    @patch("scripts.coding_discovery_tools.utils.get_user_info", return_value="root")
    def test_root_maps_to_none(self, _mock):
        self.assertIsNone(get_audit_user())

    @patch("scripts.coding_discovery_tools.utils.get_user_info", return_value="unknown")
    def test_unknown_maps_to_none(self, _mock):
        self.assertIsNone(get_audit_user())


class TestGetUserInfoGuaranteedString(unittest.TestCase):
    """get_user_info() must always return a usable string for /Users paths."""

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils.run_command", return_value="alice")
    def test_returns_string_for_normal_user(self, _cmd, _sys):
        result = get_user_info()
        self.assertEqual(result, "alice")
        self.assertIsInstance(result, str)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    @patch("scripts.coding_discovery_tools.utils.run_command", return_value=None)
    def test_falls_back_to_unknown_never_none(self, _cmd, _sys):
        # When every resolution method yields nothing, the /Users-path resolver
        # must still return a non-None string (so callers never build /Users/None).
        # getpass is imported locally inside get_user_info, so patch the stdlib module.
        with patch("getpass.getuser", return_value=""):
            result = get_user_info()
        self.assertIsNotNone(result)
        self.assertEqual(result, "unknown")


class TestStripWindowsDomain(unittest.TestCase):
    """Windows DOMAIN\\username parse yields the bare username."""

    def test_domain_prefix_stripped(self):
        self.assertEqual(_strip_windows_domain("CORP\\bob"), "bob")

    def test_machine_prefix_stripped(self):
        self.assertEqual(_strip_windows_domain("WIN-ABC\\alice"), "alice")

    def test_no_backslash_returned_unchanged(self):
        self.assertEqual(_strip_windows_domain("alice"), "alice")


if __name__ == "__main__":
    unittest.main()
