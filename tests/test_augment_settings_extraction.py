"""
Integration tests for Augment Code settings/permissions extraction (macOS).

Exercises the outermost surface (``extract_settings()``) plus the D6 regression:
``hooks`` must survive end-to-end through ``transform_settings_to_backend_format``
inside ``raw_settings`` (the transformer does NOT lift hooks). Also covers
``toolPermissions`` -> allow/deny/ask mapping, ``shellInputRegex`` retention,
scope precedence, and sad-paths (JSONC, invalid JSON in one scope, oversize,
missing).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.augment.augment_settings_extractor import (
    MacOSAugmentSettingsExtractor,
)
from scripts.coding_discovery_tools.settings_transformers import (
    transform_settings_to_backend_format,
)

_SETTINGS_MOD = "scripts.coding_discovery_tools.macos.augment.augment_settings_extractor"


def _tool_perm(tool, ptype, regex=None, event="pre"):
    entry = {"toolName": tool, "eventType": event, "permission": {"type": ptype}}
    if regex is not None:
        entry["shellInputRegex"] = regex
    return entry


class _AugmentSettingsHarness(unittest.TestCase):
    """Pins the settings extractor to a single hermetic ~/.augment, no project walk."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.augment_dir = self.user_home / ".augment"
        self.augment_dir.mkdir(parents=True)
        self.extractor = MacOSAugmentSettingsExtractor()
        # Scope to this user, no managed file, no filesystem walk.
        self._patchers = [
            patch.object(self.extractor, "_user_settings_scan",
                         side_effect=lambda fn: fn(self.user_home)),
            patch.object(self.extractor, "_managed_settings_path",
                         return_value=Path(self.tmp_dir) / "nope" / "settings.json"),
            patch.object(self.extractor, "_iter_top_level_dirs", return_value=[]),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_user_settings(self, data, filename="settings.json"):
        path = self.augment_dir / filename
        path.write_text(json.dumps(data) if isinstance(data, (dict, list)) else data,
                        encoding="utf-8")
        return path


class TestAugmentToolPermissions(_AugmentSettingsHarness):
    def test_tool_permissions_mapped_to_allow_deny_ask(self):
        self._write_user_settings({
            "toolPermissions": [
                _tool_perm("read-file", "allow"),
                _tool_perm("delete-file", "deny"),
                _tool_perm("run-shell", "ask-user"),
            ],
        })
        records = self.extractor.extract_settings()
        self.assertEqual(len(records), 1)
        perms = records[0]["permissions"]
        self.assertIn("read-file", perms["allow"])
        self.assertIn("delete-file", perms["deny"])
        self.assertIn("run-shell", perms["ask"])

    def test_shell_input_regex_appended_to_tool_name(self):
        self._write_user_settings({
            "toolPermissions": [
                _tool_perm("run-shell", "allow", regex="^git status"),
            ],
        })
        records = self.extractor.extract_settings()
        self.assertIn("run-shell(^git status)", records[0]["permissions"]["allow"])

    def test_unknown_permission_type_ignored(self):
        self._write_user_settings({
            "toolPermissions": [
                _tool_perm("x", "allow"),
                _tool_perm("y", "weird-type"),
                {"toolName": "z"},  # missing permission
            ],
        })
        perms = self.extractor.extract_settings()[0]["permissions"]
        self.assertEqual(perms["allow"], ["x"])
        self.assertEqual(perms["deny"], [])
        self.assertEqual(perms["ask"], [])


class TestAugmentHooksRegressionD6(_AugmentSettingsHarness):
    def test_hooks_preserved_in_raw_settings(self):
        self._write_user_settings({
            "toolPermissions": [_tool_perm("read", "allow")],
            "hooks": {"PreToolUse": [{"command": "echo audit"}]},
        })
        records = self.extractor.extract_settings()
        self.assertIn("hooks", records[0]["raw_settings"])

    def test_hooks_survive_transform_to_backend_format(self):
        """D6 end-to-end: the transformer does NOT lift hooks, so they MUST ride
        inside raw_settings and survive into the backend payload."""
        self._write_user_settings({
            "toolPermissions": [_tool_perm("read", "allow")],
            "hooks": {"PreToolUse": [{"command": "echo audit"}]},
        })
        records = self.extractor.extract_settings()
        backend = transform_settings_to_backend_format(records)
        self.assertIsNotNone(backend)
        self.assertEqual(
            backend["raw_settings"]["hooks"],
            {"PreToolUse": [{"command": "echo audit"}]},
        )
        # The permission was also lifted into allow_rules.
        self.assertIn("read", backend.get("allow_rules", []))


class TestAugmentScopePrecedence(_AugmentSettingsHarness):
    def test_managed_beats_user(self):
        self._write_user_settings({"toolPermissions": [_tool_perm("user-tool", "allow")]})
        managed_dir = Path(self.tmp_dir) / "etc" / "augment"
        managed_dir.mkdir(parents=True)
        managed_path = managed_dir / "settings.json"
        managed_path.write_text(json.dumps({
            "toolPermissions": [_tool_perm("managed-tool", "deny")],
        }), encoding="utf-8")
        with patch.object(self.extractor, "_managed_settings_path", return_value=managed_path):
            records = self.extractor.extract_settings()
        backend = transform_settings_to_backend_format(records)
        # Managed (precedence 4) wins over user (1).
        self.assertEqual(backend["scope"], "managed")
        self.assertIn("managed-tool", backend.get("deny_rules", []))


class TestAugmentSettingsSadPaths(_AugmentSettingsHarness):
    def test_jsonc_comments_and_trailing_commas_tolerated(self):
        self._write_user_settings(
            '{\n  // a comment\n  "toolPermissions": [\n'
            '    {"toolName": "read", "eventType": "pre", "permission": {"type": "allow"}},\n'
            '  ],\n}'
        )
        records = self.extractor.extract_settings()
        self.assertEqual(len(records), 1)
        self.assertIn("read", records[0]["permissions"]["allow"])

    def test_invalid_json_in_one_scope_skips_only_that_file(self):
        # User file is broken; a project file is valid -> only the project record.
        # The temp dir lives under /var, which the real should_skip_system_path
        # skips, so neutralise it for the project walk (mirrors the rules suite).
        self._write_user_settings("{ not json at all")
        project_dir = self.user_home / "repo" / ".augment"
        project_dir.mkdir(parents=True)
        (project_dir / "settings.json").write_text(json.dumps({
            "toolPermissions": [_tool_perm("ok", "allow")],
        }), encoding="utf-8")
        with patch.object(self.extractor, "_iter_top_level_dirs",
                          return_value=[self.user_home / "repo"]), \
             patch(f"{_SETTINGS_MOD}.should_skip_system_path", return_value=False):
            records = self.extractor.extract_settings()
        scopes = sorted(r["scope"] for r in records)
        self.assertEqual(scopes, ["project"])
        self.assertIn("ok", records[0]["permissions"]["allow"])

    def test_oversize_file_truncated_skipped(self):
        with patch(f"{_SETTINGS_MOD}._MAX_SETTINGS_BYTES", 10):
            self._write_user_settings({"toolPermissions": [_tool_perm("read", "allow")]})
            records = self.extractor.extract_settings()
        self.assertEqual(records, [])

    def test_missing_settings_yields_empty(self):
        records = self.extractor.extract_settings()
        self.assertEqual(records, [])

    def test_local_scope_from_settings_local_json(self):
        project_dir = self.user_home / "repo" / ".augment"
        project_dir.mkdir(parents=True)
        (project_dir / "settings.local.json").write_text(json.dumps({
            "toolPermissions": [_tool_perm("local-tool", "allow")],
        }), encoding="utf-8")
        with patch.object(self.extractor, "_iter_top_level_dirs",
                          return_value=[self.user_home / "repo"]), \
             patch(f"{_SETTINGS_MOD}.should_skip_system_path", return_value=False):
            records = self.extractor.extract_settings()
        self.assertTrue(any(r["scope"] == "local" for r in records))

    def test_user_home_augment_not_double_counted_as_project(self):
        """The user-home ~/.augment must be skipped by the project walk."""
        self._write_user_settings({"toolPermissions": [_tool_perm("u", "allow")]})
        # Point the project walk at the user home itself; ~/.augment must be
        # skipped by the user-augment-dir guard (not just the system-path skip,
        # so neutralise the latter to actually exercise the guard).
        with patch.object(self.extractor, "_iter_top_level_dirs",
                          return_value=[self.user_home]), \
             patch(f"{_SETTINGS_MOD}.should_skip_system_path", return_value=False):
            records = self.extractor.extract_settings()
        # Exactly one record (the user-scope one), no duplicate project record.
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scope"], "user")


if __name__ == "__main__":
    unittest.main()
