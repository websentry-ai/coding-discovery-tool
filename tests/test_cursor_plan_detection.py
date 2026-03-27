"""Tests for Cursor IDE subscription plan detection.

Covers:
- _get_cursor_db_path: locating the state.vscdb database per platform
- get_cursor_subscription_type: reading the plan string from SQLite

Uses real temporary SQLite databases for the data-path tests and
unittest.mock only for platform / filesystem gating.
"""

import sqlite3
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.utils import (
    get_cursor_subscription_type,
    _get_cursor_db_path,
)
from scripts.coding_discovery_tools.constants import CURSOR_PLAN_KEY


def _create_cursor_db(db_path: Path, key: str = CURSOR_PLAN_KEY, value=None):
    """Create a minimal Cursor state.vscdb with the ItemTable schema.

    Args:
        db_path: Where to write the SQLite file.
        key: The key to insert (or omit the row when value is _SKIP).
        value: The value to store. Pass None to skip inserting a row.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    if value is not None:
        conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


class TestGetCursorDbPath(unittest.TestCase):
    """Tests for _get_cursor_db_path platform routing."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self._tmp_dir)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_get_cursor_db_path_macos(self, _mock_sys):
        """Returns correct macOS path when state.vscdb exists."""
        db_path = (
            self.user_home / "Library" / "Application Support" / "Cursor"
            / "User" / "globalStorage" / "state.vscdb"
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("")
        result = _get_cursor_db_path(self.user_home)
        self.assertEqual(result, db_path)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Windows")
    def test_get_cursor_db_path_windows(self, _mock_sys):
        """Returns correct Windows path when state.vscdb exists."""
        db_path = (
            self.user_home / "AppData" / "Roaming" / "Cursor"
            / "User" / "globalStorage" / "state.vscdb"
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("")
        result = _get_cursor_db_path(self.user_home)
        self.assertEqual(result, db_path)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_get_cursor_db_path_file_not_exists(self, _mock_sys):
        """Returns None when state.vscdb does not exist on disk."""
        result = _get_cursor_db_path(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Linux")
    def test_get_cursor_db_path_unsupported_platform(self, _mock_sys):
        """Returns None for unsupported platform (Linux)."""
        result = _get_cursor_db_path(self.user_home)
        self.assertIsNone(result)


class TestGetCursorSubscriptionType(unittest.TestCase):
    """Tests for get_cursor_subscription_type reading plan from SQLite."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self._tmp_dir)
        # Create the DB in the macOS-style location so _get_cursor_db_path works
        self.db_path = (
            self.user_home / "Library" / "Application Support" / "Cursor"
            / "User" / "globalStorage" / "state.vscdb"
        )

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _setup_db_with_plan(self, plan_value):
        """Helper: create the DB with a specific plan value."""
        _create_cursor_db(self.db_path, value=plan_value)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_pro(self, _mock_sys):
        """Reads 'pro' plan from the database."""
        self._setup_db_with_plan("pro")
        result = get_cursor_subscription_type(self.user_home)
        self.assertEqual(result, "pro")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_enterprise(self, _mock_sys):
        """Reads 'enterprise' plan from the database."""
        self._setup_db_with_plan("enterprise")
        result = get_cursor_subscription_type(self.user_home)
        self.assertEqual(result, "enterprise")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_free(self, _mock_sys):
        """Reads 'free' plan from the database."""
        self._setup_db_with_plan("free")
        result = get_cursor_subscription_type(self.user_home)
        self.assertEqual(result, "free")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_business(self, _mock_sys):
        """Reads 'business' plan from the database."""
        self._setup_db_with_plan("business")
        result = get_cursor_subscription_type(self.user_home)
        self.assertEqual(result, "business")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_db_not_found(self, _mock_sys):
        """Returns None when the state.vscdb file does not exist."""
        # Do not create the DB file
        result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_key_not_in_db(self, _mock_sys):
        """Returns None when the plan key is missing from ItemTable."""
        _create_cursor_db(self.db_path)  # Create table but insert no rows
        result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_empty_value(self, _mock_sys):
        """Returns None when the plan value is an empty string."""
        self._setup_db_with_plan("")
        result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_whitespace_value(self, _mock_sys):
        """Returns None when the plan value is only whitespace."""
        self._setup_db_with_plan("   ")
        result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_sqlite_error(self, _mock_sys):
        """Returns None on SQLite error (corrupt database)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text("this is not a valid sqlite database")
        result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_permission_error(self, _mock_sys):
        """Returns None on PermissionError when copying the database."""
        self._setup_db_with_plan("pro")
        with patch("scripts.coding_discovery_tools.utils.shutil.copy2", side_effect=PermissionError("access denied")):
            result = get_cursor_subscription_type(self.user_home)
        self.assertIsNone(result)

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_subscription_type_bytes_value(self, _mock_sys):
        """Handles BLOB value (bytes) correctly by decoding to string."""
        self._setup_db_with_plan(b"enterprise")
        result = get_cursor_subscription_type(self.user_home)
        self.assertEqual(result, "enterprise")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_temp_file_cleanup(self, _mock_sys):
        """Temporary database file is cleaned up even on error."""
        self._setup_db_with_plan("pro")

        created_temps = []
        original_named_temp = tempfile.NamedTemporaryFile

        def tracking_temp(*args, **kwargs):
            t = original_named_temp(*args, **kwargs)
            created_temps.append(t.name)
            return t

        with patch("scripts.coding_discovery_tools.utils.tempfile.NamedTemporaryFile", side_effect=tracking_temp):
            get_cursor_subscription_type(self.user_home)

        # Verify all temp files were cleaned up
        for temp_path in created_temps:
            from pathlib import Path as P
            self.assertFalse(P(temp_path).exists(), f"Temp file was not cleaned up: {temp_path}")

    @patch("scripts.coding_discovery_tools.utils.platform.system", return_value="Darwin")
    def test_temp_file_cleanup_on_error(self, _mock_sys):
        """Temporary database file is cleaned up even when sqlite3.connect raises."""
        self._setup_db_with_plan("pro")

        created_temps = []
        original_named_temp = tempfile.NamedTemporaryFile

        def tracking_temp(*args, **kwargs):
            t = original_named_temp(*args, **kwargs)
            created_temps.append(t.name)
            return t

        with patch("scripts.coding_discovery_tools.utils.tempfile.NamedTemporaryFile", side_effect=tracking_temp):
            with patch("scripts.coding_discovery_tools.utils.sqlite3.connect", side_effect=sqlite3.Error("db error")):
                result = get_cursor_subscription_type(self.user_home)

        self.assertIsNone(result)
        for temp_path in created_temps:
            from pathlib import Path as P
            self.assertFalse(P(temp_path).exists(), f"Temp file was not cleaned up: {temp_path}")


if __name__ == "__main__":
    unittest.main()
