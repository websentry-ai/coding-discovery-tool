"""Tests for Cursor IDE permissions.json layering (WEB-4707).

Cursor's ``permissions.json`` (global ``~/.cursor/permissions.json`` plus
per-workspace ``<workspace>/.cursor/permissions.json``) is layered as a
per-field override on top of the SQLite (``state.vscdb`` -> ``composerState``)
read, collapsing into ONE effective backend record.

The merge is a post-processing layer in ``BaseCursorSettingsExtractor``:
``_apply_permissions_json_override`` runs AFTER ``_parse_composer_state``. When
no permissions.json speaks to any known field, the SQLite-derived record is
returned byte-identical.

Uses real temporary SQLite databases and a real temp filesystem tree for
workspace walks; unittest.mock only gates platform / Path.home.
"""

import json
import sqlite3
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.coding_discovery_tools.linux.cursor.settings_extractor import (
    LinuxCursorSettingsExtractor,
)
from scripts.coding_discovery_tools.macos.cursor.settings_extractor import (
    MacOSCursorSettingsExtractor,
)


STORAGE_KEY = (
    "src.vs.platform.reactivestorage.browser."
    "reactiveStorageServiceImpl.persistentStorage.applicationUser"
)


def _create_cursor_db(db_path: Path, composer_state_dict=None):
    """Create a minimal Cursor state.vscdb with a composerState row.

    Args:
        db_path: Where to write the SQLite file.
        composer_state_dict: The composerState payload. Pass None to create the
            table without inserting the applicationUser row (no-row case).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ItemTable "
        "(key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)"
    )
    if composer_state_dict is not None:
        value = json.dumps({"composerState": composer_state_dict})
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            (STORAGE_KEY, value),
        )
    conn.commit()
    conn.close()


class _BaseCursorPermissionsTest(unittest.TestCase):
    """Shared fixture: a Linux extractor whose user_home is a temp tree.

    The Linux extractor's workspace walk roots at ``user_home``, so confining
    ``user_home`` to ``tempfile.mkdtemp()`` confines the whole walk. ``Path.home``
    is patched to the same temp home and root detection is forced False so the
    SQLite path resolves deterministically.
    """

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self._tmp_dir)
        self.extractor = LinuxCursorSettingsExtractor()
        self.db_path = self.extractor._get_db_path(self.user_home)

        self._home_patch = patch(
            "scripts.coding_discovery_tools.linux.cursor.settings_extractor.Path.home",
            return_value=self.user_home,
        )
        self._root_patch = patch(
            "scripts.coding_discovery_tools.linux.cursor.settings_extractor.is_running_as_root",
            return_value=False,
        )
        # mkdtemp lands under /tmp here, which the Linux walk treats as a skipped
        # system dir (real workspaces live under /home/<user>, which is not
        # skipped). Neutralize that system-skip for paths inside our temp home so
        # the real walk logic (recursion, depth limits, .cursor matching, global
        # skip) is exercised against the fixture tree.
        from scripts.coding_discovery_tools import linux_extraction_helpers as _leh
        home_str = str(self.user_home)
        _orig_skip = _leh.should_skip_system_path

        def _skip_outside_home(path):
            path_str = str(path)
            if path_str == home_str or path_str.startswith(home_str + "/"):
                return False
            return _orig_skip(path)

        self._skip_patch = patch.object(
            _leh, "should_skip_system_path", side_effect=_skip_outside_home
        )
        self._home_patch.start()
        self._root_patch.start()
        self._skip_patch.start()

    def tearDown(self):
        self._skip_patch.stop()
        self._root_patch.stop()
        self._home_patch.stop()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------

    def _write_global_permissions(self, payload, raw: str = None):
        """Write ~/.cursor/permissions.json. raw overrides json.dumps(payload)."""
        path = self.user_home / ".cursor" / "permissions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
        return path

    def _write_workspace_permissions(self, workspace_name, payload, raw: str = None):
        """Write <workspace>/.cursor/permissions.json under the temp home."""
        path = self.user_home / workspace_name / ".cursor" / "permissions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
        return path

    def _extract(self):
        """Run the full SQLite + permissions.json override pipeline."""
        return self.extractor._extract_from_database(self.db_path, self.user_home)

    def _parse_only(self, composer_state):
        """Direct ``_parse_composer_state`` output (no permissions.json layer)."""
        return self.extractor._parse_composer_state(composer_state, self.db_path)


# ===========================================================================
# R: file-absent / read-failure parity (byte-identical guarantee)
# ===========================================================================

class TestPermissionsAbsentParity(_BaseCursorPermissionsTest):

    def test_R1_file_absent_equals_parse_composer_state(self):
        """No permissions.json -> output identical to direct _parse_composer_state."""
        composer = {"useYoloMode": True, "yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        self.assertEqual(self._extract(), self._parse_only(composer))

    def test_R2_no_sqlite_row_returns_none(self):
        """No applicationUser row -> None (nothing to override)."""
        _create_cursor_db(self.db_path, composer_state_dict=None)
        self.assertIsNone(self._extract())

    def test_R3_empty_permissions_file_equals_parse(self):
        """Empty {} permissions.json speaks to no field -> unchanged."""
        composer = {"useYoloMode": False, "yoloCommandAllowlist": ["git"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({})
        self.assertEqual(self._extract(), self._parse_only(composer))

    def test_R4_invalid_json_file_falls_back_to_parse(self):
        """Unparseable permissions.json -> ignored, output unchanged."""
        composer = {"useYoloMode": False, "yoloCommandAllowlist": ["git"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions(None, raw="{ this is : not json ]")
        self.assertEqual(self._extract(), self._parse_only(composer))


# ===========================================================================
# M: mcpAllowlist override
# ===========================================================================

class TestMcpAllowlistOverride(_BaseCursorPermissionsTest):

    def test_M1_mcp_replaces_sqlite_value(self):
        """mcpAllowlist in file replaces the SQLite-derived mcp_tool_allowlist."""
        composer = {"mcpAllowlist": ["serverA:tool1", "serverA:tool2"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": ["serverB:only"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["serverB:only"])

    def test_M2_mcp_present_no_sqlite_value(self):
        """mcpAllowlist applies even when SQLite had none."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": ["serverB:only"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["serverB:only"])

    def test_M3_mcp_untouched_when_file_silent(self):
        """File without mcpAllowlist leaves the SQLite mcp value intact."""
        composer = {"mcpAllowlist": ["serverA:tool1"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": ["npm"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["serverA:tool1"])

    def test_M4_user_then_workspace_concat_drops_sqlite(self):
        """User file + workspace file concat (order-preserved); SQLite dropped."""
        composer = {"mcpAllowlist": ["sqlite:tool"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": ["u:one", "u:two"]})
        self._write_workspace_permissions("projX", {"mcpAllowlist": ["w:three"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["u:one", "u:two", "w:three"])
        self.assertNotIn("sqlite:tool", result["mcp_tool_allowlist"])

    def test_M5_empty_mcp_array_replaces_to_empty(self):
        """mcpAllowlist: [] is a present key -> intentional override-to-empty.

        A present (even empty) mcpAllowlist overrides entirely (correct Cursor
        semantics), so the SQLite-derived mcp_tool_allowlist is wiped to [].
        """
        composer = {"mcpAllowlist": ["sqlite:tool"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": []})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], [])


# ===========================================================================
# T: terminalAllowlist -> Bash(cmd*) (no trailing space), distinct from SQLite
# ===========================================================================

class TestTerminalAllowlistOverride(_BaseCursorPermissionsTest):

    def test_T1_terminal_maps_to_bash_no_space(self):
        """['npm','git'] -> ['Bash(npm*)','Bash(git*)'] with NO trailing space."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": ["npm", "git"]})
        result = self._extract()
        self.assertEqual(result["allow_rules"], ["Bash(npm*)", "Bash(git*)"])

    def test_T2_sqlite_only_keeps_space_form(self):
        """SQLite yoloCommandAllowlist keeps 'Bash(ls *)' WITH trailing space."""
        composer = {"yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        result = self._extract()  # no permissions.json
        self.assertEqual(result["allow_rules"], ["Bash(ls *)"])

    def test_T3_file_replaces_sqlite_distinct_encoding(self):
        """File terminal rules REPLACE the SQLite command rules (encodings differ)."""
        composer = {"yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": ["ls"]})
        result = self._extract()
        self.assertEqual(result["allow_rules"], ["Bash(ls*)"])
        self.assertNotIn("Bash(ls *)", result["allow_rules"])

    def test_T4_terminal_concat_user_and_workspace(self):
        """Terminal entries concat across user + workspace, order-preserved."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": ["npm"]})
        self._write_workspace_permissions("projX", {"terminalAllowlist": ["git"]})
        result = self._extract()
        self.assertEqual(result["allow_rules"], ["Bash(npm*)", "Bash(git*)"])

    def test_T5_empty_terminal_array_replaces_to_empty(self):
        """terminalAllowlist: [] is a present key -> intentional override-to-empty.

        A present (even empty) terminalAllowlist overrides entirely (correct
        Cursor semantics), so the SQLite-derived yolo allow_rules are wiped to [].
        """
        composer = {"yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": []})
        result = self._extract()
        self.assertEqual(result["allow_rules"], [])


# ===========================================================================
# A: autoRun written verbatim into raw_settings
# ===========================================================================

class TestAutoRunOverride(_BaseCursorPermissionsTest):

    def test_A1_autorun_written_into_raw_settings(self):
        """autoRun object lands verbatim in raw_settings['autoRun']."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        auto_run = {"allow_instructions": ["build"], "block_instructions": ["rm"]}
        self._write_global_permissions({"autoRun": auto_run})
        result = self._extract()
        self.assertEqual(
            result["raw_settings"]["autoRun"],
            {"allow_instructions": ["build"], "block_instructions": ["rm"]},
        )

    def test_A2_autorun_nested_arrays_concat(self):
        """Nested allow/block_instructions concat across user + workspace."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions(
            {"autoRun": {"allow_instructions": ["build"], "block_instructions": ["rm"]}}
        )
        self._write_workspace_permissions(
            "projX",
            {"autoRun": {"allow_instructions": ["test"], "block_instructions": ["sudo"]}},
        )
        result = self._extract()
        self.assertEqual(
            result["raw_settings"]["autoRun"],
            {
                "allow_instructions": ["build", "test"],
                "block_instructions": ["rm", "sudo"],
            },
        )

    def test_A3_autorun_does_not_disturb_mcp_or_terminal(self):
        """An autoRun-only file leaves mcp/terminal fields from SQLite intact."""
        composer = {"mcpAllowlist": ["s:t"], "yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions(
            {"autoRun": {"allow_instructions": ["build"]}}
        )
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["s:t"])
        self.assertEqual(result["allow_rules"], ["Bash(ls *)"])
        self.assertEqual(
            result["raw_settings"]["autoRun"],
            {"allow_instructions": ["build"], "block_instructions": []},
        )

    def test_A6_malformed_autorun_skipped_no_raise(self):
        """autoRun as a non-object is ignored without raising; output unchanged."""
        composer = {"useYoloMode": False, "yoloCommandAllowlist": ["git"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"autoRun": "not-an-object"})
        result = self._extract()
        self.assertEqual(result, self._parse_only(composer))

    def test_A7_autorun_unknown_subkey_preserved(self):
        """Unknown autoRun sub-keys survive the merge alongside the arrays."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions(
            {"autoRun": {"allow_instructions": ["x"], "mode": "smart"}}
        )
        result = self._extract()
        auto_run = result["raw_settings"]["autoRun"]
        self.assertEqual(auto_run["mode"], "smart")
        self.assertEqual(auto_run["allow_instructions"], ["x"])


# ===========================================================================
# I: field independence / unknown keys
# ===========================================================================

class TestFieldIndependence(_BaseCursorPermissionsTest):

    def test_I1_mcp_only_file_leaves_terminal_from_sqlite(self):
        """mcp-only file overrides mcp; terminal stays from SQLite."""
        composer = {"mcpAllowlist": ["old:t"], "yoloCommandAllowlist": ["ls"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": ["new:t"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["new:t"])
        self.assertEqual(result["allow_rules"], ["Bash(ls *)"])

    def test_I2_unknown_top_level_key_ignored(self):
        """Unknown keys in permissions.json are ignored (no error, no leakage)."""
        composer = {"useYoloMode": False, "yoloCommandAllowlist": ["git"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"somethingNew": {"a": 1}, "other": [1, 2]})
        result = self._extract()
        self.assertEqual(result, self._parse_only(composer))


# ===========================================================================
# W: workspace-only / global-not-double-counted
# ===========================================================================

class TestWorkspaceScoping(_BaseCursorPermissionsTest):

    def test_W1_workspace_file_no_user_file(self):
        """A workspace file applies even with no global ~/.cursor file."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_workspace_permissions("projX", {"mcpAllowlist": ["w:tool"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["w:tool"])

    def test_W2_global_cursor_not_double_counted(self):
        """The global ~/.cursor/permissions.json is counted once, not twice.

        The workspace iterator must NOT re-yield the global file (de-dupe would
        otherwise mask a double-walk), and the effective record carries it once.
        """
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        global_path = self._write_global_permissions({"mcpAllowlist": ["g:one"]})
        walked = list(self.extractor._iter_workspace_permissions_files(self.user_home))
        self.assertNotIn(global_path, walked)
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["g:one"])


# ===========================================================================
# D: cross-workspace de-dupe / order preservation
# ===========================================================================

class TestCrossWorkspaceDedupe(_BaseCursorPermissionsTest):

    def test_D1_duplicate_across_workspaces_deduped_order_preserved(self):
        """Same entry in two workspaces collapses once, first-seen order kept."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"mcpAllowlist": ["a", "b"]})
        self._write_workspace_permissions("projX", {"mcpAllowlist": ["b", "c"]})
        self._write_workspace_permissions("projY", {"mcpAllowlist": ["a", "d"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["a", "b", "c", "d"])


# ===========================================================================
# J: JSONC tolerance
# ===========================================================================

class TestJsoncTolerance(_BaseCursorPermissionsTest):

    def test_J1_jsonc_comments_and_trailing_comma_parse(self):
        """// and /* */ comments plus a trailing comma parse successfully."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        raw = (
            "{\n"
            "  // global mcp allowlist\n"
            "  \"mcpAllowlist\": [\n"
            "    \"s:tool\", /* inline */\n"
            "  ],\n"
            "}\n"
        )
        self._write_global_permissions(None, raw=raw)
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["s:tool"])


# ===========================================================================
# E: one invalid + one valid workspace file -> valid applied, no raise
# ===========================================================================

class TestWorkspaceErrorIsolation(_BaseCursorPermissionsTest):

    def test_E1_one_bad_workspace_does_not_poison_valid(self):
        """A broken workspace file is skipped; a sibling valid one still applies."""
        composer = {"useYoloMode": False}
        _create_cursor_db(self.db_path, composer)
        self._write_workspace_permissions("badproj", None, raw="{ broken ]")
        self._write_workspace_permissions("goodproj", {"mcpAllowlist": ["ok:tool"]})
        result = self._extract()
        self.assertEqual(result["mcp_tool_allowlist"], ["ok:tool"])


# ===========================================================================
# S: shape of the collapsed record
# ===========================================================================

class TestCollapsedRecordShape(_BaseCursorPermissionsTest):

    def test_S1_single_dict_with_expected_scope_keys(self):
        """Output is a single dict with valid settings_source, scope, settings_path."""
        composer = {"mcpAllowlist": ["s:t"]}
        _create_cursor_db(self.db_path, composer)
        self._write_global_permissions({"terminalAllowlist": ["npm"]})
        result = self._extract()
        self.assertIsInstance(result, dict)
        self.assertIn(result["settings_source"], {"user", "project", "managed"})
        self.assertIn("scope", result)
        self.assertIn("settings_path", result)


# ===========================================================================
# Cross-OS smoke: macOS extractor honors the same global override
# ===========================================================================

class TestMacOSGlobalOverride(unittest.TestCase):
    """macOS extractor: global ~/.cursor/permissions.json layering.

    The macOS workspace walk now roots at ``user_home`` (matching the Linux
    sibling). Confining ``user_home`` to a temp tree confines the whole walk,
    and the global ~/.cursor/permissions.json is read directly via
    ``_get_user_permissions_path`` (not via the walk), so the assertion holds.
    """

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self._tmp_dir)
        self.extractor = MacOSCursorSettingsExtractor()
        self.db_path = self.extractor._get_db_path(self.user_home)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_macos_global_mcp_override(self):
        """macOS reads ~/.cursor/permissions.json and overrides mcp_tool_allowlist."""
        composer = {"mcpAllowlist": ["sqlite:tool"]}
        _create_cursor_db(self.db_path, composer)
        perms = self.user_home / ".cursor" / "permissions.json"
        perms.parent.mkdir(parents=True, exist_ok=True)
        perms.write_text(json.dumps({"mcpAllowlist": ["mac:tool"]}), encoding="utf-8")

        result = self.extractor._extract_from_database(self.db_path, self.user_home)
        self.assertEqual(result["mcp_tool_allowlist"], ["mac:tool"])


if __name__ == "__main__":
    unittest.main()
