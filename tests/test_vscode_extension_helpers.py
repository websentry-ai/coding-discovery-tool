"""Tests for the shared VS Code extensions-registry helper.

``find_extension_in_editor`` is the new gate for the extension-based detectors
(Cline / Roo / Kilo): it returns ``(location, version)`` only when the extension
is a LIVE entry in the editor's ``extensions.json`` (which VS Code rewrites on
uninstall), not when its globalStorage residue survives. These tests pin both
directions plus the case-insensitive id match and the never-raise contract.

Hermetic tmp dirs, pure ``pathlib`` + JSON — no platform skip (the registry
layout is identical on every OS, so the gate must be exercised on every CI box).
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.vscode_extension_helpers import (
    extensions_dir_for_editor,
    find_extension_in_editor,
)

CLINE_EXT_ID = "saoudrizwan.claude-dev"
KILO_EXT_ID = "kilocode.Kilo-Code"


class TestExtensionsDirForEditor(unittest.TestCase):
    def setUp(self):
        self.home = Path("/home/u")

    def test_known_editors_map_to_their_dirs(self):
        self.assertEqual(
            extensions_dir_for_editor(self.home, "Code"),
            self.home / ".vscode" / "extensions",
        )
        self.assertEqual(
            extensions_dir_for_editor(self.home, "Cursor"),
            self.home / ".cursor" / "extensions",
        )
        self.assertEqual(
            extensions_dir_for_editor(self.home, "Windsurf"),
            self.home / ".windsurf" / "extensions",
        )
        self.assertEqual(
            extensions_dir_for_editor(self.home, "VSCodium"),
            self.home / ".vscode-oss" / "extensions",
        )
        self.assertEqual(
            extensions_dir_for_editor(self.home, "Antigravity"),
            self.home / ".antigravity" / "extensions",
        )

    def test_unknown_editor_returns_none(self):
        self.assertIsNone(extensions_dir_for_editor(self.home, "Emacs"))


class TestFindExtensionInEditor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_registry(self, ide_key: str, entries) -> Path:
        ext_dir = extensions_dir_for_editor(self.home, ide_key)
        ext_dir.mkdir(parents=True, exist_ok=True)
        registry = ext_dir / "extensions.json"
        registry.write_text(json.dumps(entries), encoding="utf-8")
        return registry

    # --- entry present -> (location, version) ----------------------------

    def test_entry_present_returns_location_and_version(self):
        self._write_registry("Code", [
            {
                "identifier": {"id": CLINE_EXT_ID},
                "version": "3.7.0",
                "relativeLocation": f"{CLINE_EXT_ID}-3.7.0",
            }
        ])
        result = find_extension_in_editor(self.home, "Code", CLINE_EXT_ID)
        self.assertIsNotNone(result)
        location, version = result
        self.assertEqual(version, "3.7.0")
        self.assertEqual(
            location,
            str(self.home / ".vscode" / "extensions" / f"{CLINE_EXT_ID}-3.7.0"),
        )

    def test_entry_present_absolute_location_path_preferred(self):
        self._write_registry("Cursor", [
            {
                "identifier": {"id": CLINE_EXT_ID},
                "version": "1.0.0",
                "location": {"path": "/abs/ext/path"},
            }
        ])
        location, version = find_extension_in_editor(self.home, "Cursor", CLINE_EXT_ID)
        self.assertEqual(location, "/abs/ext/path")
        self.assertEqual(version, "1.0.0")

    def test_entry_present_without_version_returns_none_version(self):
        self._write_registry("Code", [{"identifier": {"id": CLINE_EXT_ID}}])
        location, version = find_extension_in_editor(self.home, "Code", CLINE_EXT_ID)
        self.assertIsNone(version)
        # Falls back to the extensions dir itself when no location is recorded.
        self.assertEqual(location, str(self.home / ".vscode" / "extensions"))

    # --- absent / empty / corrupt -> None --------------------------------

    def test_no_registry_file_returns_none(self):
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))

    def test_empty_registry_returns_none(self):
        self._write_registry("Code", [])
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))

    def test_registry_without_matching_entry_returns_none(self):
        self._write_registry("Code", [
            {"identifier": {"id": "some.other-extension"}, "version": "1.0.0"}
        ])
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))

    def test_corrupt_json_returns_none(self):
        ext_dir = extensions_dir_for_editor(self.home, "Code")
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text("not valid json {{{", encoding="utf-8")
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))

    def test_non_list_json_returns_none(self):
        ext_dir = extensions_dir_for_editor(self.home, "Code")
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text('{"identifier": {}}', encoding="utf-8")
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))

    # --- case-insensitive id match (both directions) ---------------------

    def test_case_insensitive_stored_mixed_query_lower(self):
        """Registry stores ``kilocode.Kilo-Code``; querying the lowercase id matches."""
        self._write_registry("Code", [
            {"identifier": {"id": "kilocode.Kilo-Code"}, "version": "4.0.0"}
        ])
        result = find_extension_in_editor(self.home, "Code", "kilocode.kilo-code")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "4.0.0")

    def test_case_insensitive_stored_lower_query_mixed(self):
        """Registry stores ``kilocode.kilo-code``; querying the display-cased id matches."""
        self._write_registry("Code", [
            {"identifier": {"id": "kilocode.kilo-code"}, "version": "4.1.0"}
        ])
        result = find_extension_in_editor(self.home, "Code", KILO_EXT_ID)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "4.1.0")

    # --- unknown editor -> None ------------------------------------------

    def test_unknown_editor_returns_none(self):
        self.assertIsNone(find_extension_in_editor(self.home, "Emacs", CLINE_EXT_ID))


if __name__ == "__main__":
    unittest.main()
