"""Windsurf ships as Devin after the rebrand; both layouts must be detected.

The bundle, the Windows executable, the Linux binary and the per-user data
folder were all renamed. Existing installs keep the old names, so every check
has to accept either without turning one editor into two.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.coding_discovery_tools.vscode_extension_helpers import (  # noqa: E402
    VSCODE_EDITOR_DISPLAY_NAMES, VSCODE_EDITOR_KEYS, extensions_dir_for_editor,
)


class ExtensionsDirSurvivesTheRename(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_legacy_windsurf_dir_is_still_returned(self):
        legacy = self.home / ".windsurf" / "extensions"
        legacy.mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), legacy)

    def test_renamed_devin_dir_is_found(self):
        devin = self.home / ".devin" / "extensions"
        devin.mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), devin)

    def test_legacy_wins_when_both_exist(self):
        legacy = self.home / ".windsurf" / "extensions"
        legacy.mkdir(parents=True)
        (self.home / ".devin" / "extensions").mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), legacy)

    def test_absent_both_falls_back_to_the_legacy_path(self):
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"),
                         self.home / ".windsurf" / "extensions")

    def test_the_rename_did_not_create_a_second_editor(self):
        self.assertNotIn("Devin", VSCODE_EDITOR_KEYS)
        self.assertNotIn("Devin", VSCODE_EDITOR_DISPLAY_NAMES.values())
        self.assertEqual(VSCODE_EDITOR_DISPLAY_NAMES["Windsurf"], "Windsurf")


class DetectorsAcceptBothNames(unittest.TestCase):

    def test_macos_detector_lists_both_bundles(self):
        from scripts.coding_discovery_tools.macos.windsurf.windsurf import (
            MacOSWindsurfDetector,
        )
        self.assertEqual(MacOSWindsurfDetector.DEFAULT_APP_PATH.name, "Windsurf.app")
        self.assertEqual(MacOSWindsurfDetector.REBRANDED_APP_PATH.name, "Devin.app")

    def test_linux_detector_accepts_the_devin_binary(self):
        from scripts.coding_discovery_tools.linux.windsurf import windsurf as lw
        names = {p.name for p in lw._SYSTEM_PATHS} | {p.name for p in lw._USER_RELATIVE_PATHS}
        self.assertIn("windsurf", names)
        self.assertIn("devin", names)

    def test_host_ide_lists_include_the_renamed_bundle(self):
        from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
        from scripts.coding_discovery_tools.macos.roo_code.roo_code import (
            MacOSRooDetector,
        )
        for cls in (MacOSClineDetector, MacOSRooDetector):
            apps = cls.IDE_APP_NAMES["Windsurf"]
            self.assertIn("Windsurf.app", apps, cls.__name__)
            self.assertIn("Devin.app", apps, cls.__name__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
