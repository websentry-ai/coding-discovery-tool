"""Contract tests for the hot skip-path predicates.

``should_skip_path`` / ``should_skip_system_path`` run on every entry of every walk,
so they were rewritten to use C-level ``str.startswith(tuple)`` / ``set.isdisjoint``
instead of Python generator loops. These tests pin the exact behavior so the speedup
can never quietly change what gets skipped (i.e. what gets discovered).
"""

import os
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.macos_extraction_helpers import (
    should_skip_path,
    should_skip_system_path as mac_should_skip_system_path,
)
from scripts.coding_discovery_tools.linux_extraction_helpers import (
    should_skip_system_path as linux_should_skip_system_path,
)
from scripts.coding_discovery_tools.constants import (
    SKIP_DIRS,
    SKIP_SYSTEM_DIRS,
)
from scripts.coding_discovery_tools.linux_extraction_helpers import _LINUX_SKIP_SYSTEM_DIRS


class TestShouldSkipPath(unittest.TestCase):
    def test_skips_when_any_component_is_a_skip_dir(self):
        self.assertTrue(should_skip_path(Path("/home/alice/repo/node_modules/pkg")))
        self.assertTrue(should_skip_path(Path("/home/alice/repo/.git/objects")))
        self.assertTrue(should_skip_path(Path("/home/alice/proj/__pycache__")))

    def test_keeps_normal_project_paths(self):
        self.assertFalse(should_skip_path(Path("/home/alice/repo/src/app")))
        self.assertFalse(should_skip_path(Path("/home/alice/.claude/skills/x")))

    def test_substring_of_skip_dir_is_not_a_component_match(self):
        # "node_modules" as a substring of a real dir name must NOT match — only a
        # whole path component counts.
        self.assertFalse(should_skip_path(Path("/home/alice/node_modules_backup/x")))

    def test_matches_every_skip_dir_as_a_component(self):
        for d in SKIP_DIRS:
            self.assertTrue(should_skip_path(Path("/home/alice") / d / "child"))


@unittest.skipUnless(os.name == "posix", "macOS/Linux system-path predicate")
class TestMacSystemPath(unittest.TestCase):
    def test_skips_system_prefixes(self):
        self.assertTrue(mac_should_skip_system_path(Path("/System/Library/x")))
        self.assertTrue(mac_should_skip_system_path(Path("/usr/local/bin")))

    def test_keeps_user_paths(self):
        self.assertFalse(mac_should_skip_system_path(Path("/Users/alice/projects")))

    def test_prefix_semantics_match_startswith(self):
        # macOS uses raw startswith (no trailing slash), so a sibling that merely
        # shares the prefix string is also skipped — pin that exact behavior.
        for d in SKIP_SYSTEM_DIRS:
            self.assertTrue(mac_should_skip_system_path(Path(d)))
            self.assertTrue(mac_should_skip_system_path(Path(d + "foo")))


@unittest.skipUnless(os.name == "posix", "macOS/Linux system-path predicate")
class TestLinuxSystemPath(unittest.TestCase):
    def test_skips_exact_and_nested_system_dirs(self):
        self.assertTrue(linux_should_skip_system_path(Path("/proc")))
        self.assertTrue(linux_should_skip_system_path(Path("/usr/lib/python3")))
        self.assertTrue(linux_should_skip_system_path(Path("/var/log")))

    def test_keeps_home_and_lookalikes(self):
        self.assertFalse(linux_should_skip_system_path(Path("/home/alice")))
        # Linux uses component-boundary semantics: "/usrlocal" must NOT match "/usr".
        self.assertFalse(linux_should_skip_system_path(Path("/usrlocal/bin")))

    def test_matches_every_system_dir_exact_and_nested(self):
        for d in _LINUX_SKIP_SYSTEM_DIRS:
            self.assertTrue(linux_should_skip_system_path(Path(d)))
            self.assertTrue(linux_should_skip_system_path(Path(d + "/child")))


if __name__ == "__main__":
    unittest.main()
