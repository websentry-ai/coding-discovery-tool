"""An account's home is NFSHomeDirectory, not /Users/<name>.

Walking /Users alone misses accounts whose home sits elsewhere (network homes,
MDM-created admins), and scans a stale /Users dir for anyone whose home moved.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    DsclBatchData,
    get_all_users_macos,
    macos_home_for_user,
)

_REAL_ITERDIR = Path.iterdir
_REAL_EXISTS = Path.exists


class TestMacosHomeForUser(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        utils_mod._macos_home_map.cache_clear()
        self.addCleanup(utils_mod._macos_home_map.cache_clear)

    def test_uses_nfs_home_when_it_differs(self):
        with patch.object(utils_mod, "_macos_home_map", return_value={"admin": "/private/var/admin"}):
            self.assertEqual(Path("/private/var/admin"), macos_home_for_user("admin"))

    def test_falls_back_to_convention_when_unknown(self):
        with patch.object(utils_mod, "_macos_home_map", return_value={}):
            self.assertEqual(Path("/Users/alice"), macos_home_for_user("alice"))

    def test_conventional_home_is_unchanged(self):
        with patch.object(utils_mod, "_macos_home_map", return_value={"alice": "/Users/alice"}):
            self.assertEqual(Path("/Users/alice"), macos_home_for_user("alice"))


class TestEnumerationPicksUpOutsideHomes(unittest.TestCase):
    """/Users is stubbed empty so these assert only the new append step."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        utils_mod._macos_home_map.cache_clear()
        self.addCleanup(utils_mod._macos_home_map.cache_clear)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.outside = Path(self.tmp) / "var" / "admin"
        self.outside.mkdir(parents=True)

    def _run(self, home_map, hidden=frozenset()):
        batch = DsclBatchData(
            uid_map={"admin": 506},
            shell_map={"admin": "/bin/zsh"},
            hidden_set=hidden,
        )

        def fake_iterdir(path):
            return iter([]) if str(path) == "/Users" else _REAL_ITERDIR(path)

        def fake_exists(path):
            return True if str(path) == "/Users" else _REAL_EXISTS(path)

        with patch.object(utils_mod.platform, "system", return_value="Darwin"), \
             patch.object(utils_mod, "_fetch_dscl_batch_data", return_value=batch), \
             patch.object(utils_mod, "_macos_home_map", return_value=home_map), \
             patch.object(Path, "iterdir", fake_iterdir), \
             patch.object(Path, "exists", fake_exists):
            return get_all_users_macos()

    def test_account_outside_users_is_enumerated(self):
        self.assertEqual(["admin"], self._run({"admin": str(self.outside)}))

    def test_hidden_account_is_skipped(self):
        self.assertEqual([], self._run({"admin": str(self.outside)}, hidden=frozenset({"admin"})))

    def test_missing_home_dir_is_skipped(self):
        self.assertEqual([], self._run({"admin": str(Path(self.tmp) / "gone")}))

    def test_home_under_users_is_left_to_the_directory_walk(self):
        self.assertEqual([], self._run({"admin": "/Users/admin"}))


if __name__ == "__main__":
    unittest.main()
