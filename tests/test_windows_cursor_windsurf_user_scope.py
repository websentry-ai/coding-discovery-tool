"""User-scoping tests for the Windows Cursor / Windsurf search paths.

``detect_tool_for_user`` sets ``detector.user_home`` to the user being scanned,
but both detectors built their candidate list from ``Path.home()`` — the
scanner's own home. Under a logged-in scan that credited one user's install to
every other profile on the box; under SYSTEM it resolved to
``systemprofile`` and found nothing. These tests prove the list now follows
``user_home`` and still falls back when it is unset.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.windows.cursor.cursor import WindowsCursorDetector
from scripts.coding_discovery_tools.windows.windsurf.windsurf import WindowsWindsurfDetector


class _UserScopeMixin:

    def test_search_paths_follow_user_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            user_home = Path(tmp) / "Bob"
            detector = self.detector_cls()
            detector.user_home = user_home
            paths = detector._get_search_paths()

        appdata = [p for p in paths if "AppData" in p.parts]
        self.assertTrue(appdata)
        for path in appdata:
            self.assertTrue(str(path).startswith(str(user_home)), path)
            self.assertFalse(str(path).startswith(str(Path.home())), path)

    def test_falls_back_to_path_home_when_unset(self):
        detector = self.detector_cls()
        paths = detector._get_search_paths()

        appdata = [p for p in paths if "AppData" in p.parts]
        self.assertTrue(appdata)
        for path in appdata:
            self.assertTrue(str(path).startswith(str(Path.home())), path)

    def test_machine_global_paths_are_unscoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            detector = self.detector_cls()
            detector.user_home = Path(tmp) / "Bob"
            paths = detector._get_search_paths()

        self.assertTrue([p for p in paths if "Program Files" in str(p)])


class TestWindowsCursorUserScope(_UserScopeMixin, unittest.TestCase):
    detector_cls = WindowsCursorDetector


class TestWindowsWindsurfUserScope(_UserScopeMixin, unittest.TestCase):
    detector_cls = WindowsWindsurfDetector


if __name__ == "__main__":
    unittest.main()
