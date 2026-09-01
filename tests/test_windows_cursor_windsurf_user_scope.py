"""User-scoping tests for the Windows Cursor / Windsurf search paths.

``detect_tool_for_user`` sets ``detector.user_home`` to the user being scanned,
but both detectors built their candidate list from ``Path.home()`` — the
scanner's own home. Under a logged-in scan that credited one user's install to
every other profile on the box; under SYSTEM it resolved to ``systemprofile``
and found nothing.

The homes here are synthetic (never ``tempfile``, whose Windows root lives
*under* ``Path.home()``) so the assertions mean the same thing on every OS.
The install fixtures do touch disk, because ``detect()`` stats them.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.user_tool_detector import detect_tool_for_user
from scripts.coding_discovery_tools.windows.cursor.cursor import WindowsCursorDetector
from scripts.coding_discovery_tools.windows.windsurf.windsurf import WindowsWindsurfDetector

SYNTHETIC_HOME = Path("Z:\\profiles\\Bob")


class _SearchPathMixin:

    def test_search_paths_follow_user_home(self):
        detector = self.detector_cls()
        detector.user_home = SYNTHETIC_HOME
        paths = detector._get_search_paths()

        expected = SYNTHETIC_HOME / "AppData" / "Local" / "Programs" / self.programs_dir
        self.assertIn(expected, paths)

    def test_falls_back_to_path_home_when_unset(self):
        detector = self.detector_cls()
        paths = detector._get_search_paths()

        expected = Path.home() / "AppData" / "Local" / "Programs" / self.programs_dir
        self.assertIn(expected, paths)

    def test_machine_global_paths_are_not_scoped_to_user_home(self):
        detector = self.detector_cls()
        detector.user_home = SYNTHETIC_HOME
        paths = detector._get_search_paths()

        program_files = [p for p in paths if "Program Files" in str(p)]
        self.assertTrue(program_files)
        for path in program_files:
            self.assertNotIn(SYNTHETIC_HOME.name, path.parts)


class _CrossUserMixin:
    """The defect as it appeared in production: user A's install must not be
    reported for user B. Exercised through ``detect_tool_for_user``, the entry
    point the per-user scan loop actually uses."""

    def _install(self, user_home: Path) -> None:
        install = user_home / "AppData" / "Local" / "Programs" / self.programs_dir
        install.mkdir(parents=True)
        (install / self.exe_name).write_text("")

    def test_install_is_not_credited_to_another_user(self):
        if detect_tool_for_user(self.detector_cls(), SYNTHETIC_HOME) is not None:
            self.skipTest("machine-wide install present; per-user scoping is not the deciding factor")

        with tempfile.TemporaryDirectory() as tmp:
            home_a = Path(tmp) / "alice"
            home_b = Path(tmp) / "bob"
            home_b.mkdir(parents=True)
            self._install(home_a)

            found_a = detect_tool_for_user(self.detector_cls(), home_a)
            found_b = detect_tool_for_user(self.detector_cls(), home_b)

        self.assertIsNotNone(found_a)
        self.assertIsNone(found_b)


class TestWindowsCursorUserScope(_SearchPathMixin, _CrossUserMixin, unittest.TestCase):
    detector_cls = WindowsCursorDetector
    programs_dir = "cursor"
    exe_name = "Cursor.exe"


class TestWindowsWindsurfUserScope(_SearchPathMixin, _CrossUserMixin, unittest.TestCase):
    detector_cls = WindowsWindsurfDetector
    programs_dir = "Windsurf"
    exe_name = "Windsurf.exe"


if __name__ == "__main__":
    unittest.main()
