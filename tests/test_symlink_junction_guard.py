"""
Unit tests for ``is_symlink_or_junction`` — the traversal guard used by the skills
walks and the shared extraction engine.

``Path.is_symlink()`` returns False for an NTFS *directory junction*
(``IO_REPARSE_TAG_MOUNT_POINT``), which any user can create with ``mklink /J``.
``Path.is_junction()`` is 3.12+ and this project supports 3.9+, so the reparse tag
is inspected via ``lstat``. Windows behaviour is simulated by faking
``os.lstat``'s ``st_reparse_tag`` so these run on any platform.
"""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools import constants
from scripts.coding_discovery_tools.constants import is_symlink_or_junction

_TAG_MOUNT_POINT = 0xA0000003   # NTFS directory junction
_TAG_SYMLINK = 0xA000000C       # NTFS symlink
_TAG_CLOUD = 0x9000001A         # OneDrive-style cloud placeholder (NOT a redirect)


class _FakeStat:
    """Windows lstat result: a DIRECTORY (S_ISLNK false) carrying a reparse tag —
    exactly how a junction presents, which is why S_ISLNK alone misses it."""

    def __init__(self, tag):
        self.st_mode = stat.S_IFDIR | 0o755
        self.st_reparse_tag = tag


class TestPosixBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_regular_dir_is_not_a_link(self):
        d = self.tmp / "real"
        d.mkdir()
        self.assertFalse(is_symlink_or_junction(d))

    def test_regular_file_is_not_a_link(self):
        f = self.tmp / "f.md"
        f.write_text("x", encoding="utf-8")
        self.assertFalse(is_symlink_or_junction(f))

    def test_posix_symlink_detected(self):
        target = self.tmp / "target"
        target.mkdir()
        link = self.tmp / "link"
        os.symlink(target, link)
        self.assertTrue(is_symlink_or_junction(link))


class TestWindowsReparseTags(unittest.TestCase):
    """Simulate Windows: is_symlink() is False for a junction, but lstat exposes the tag."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "dir"
        self.d.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _guard_with_tag(self, tag):
        with patch.object(constants.os, "lstat", return_value=_FakeStat(tag)):
            return is_symlink_or_junction(self.d)

    def test_junction_detected_even_though_is_symlink_false(self):
        # This is the exact bypass the reviewer flagged.
        self.assertFalse(self.d.is_symlink())
        self.assertTrue(self._guard_with_tag(_TAG_MOUNT_POINT))

    def test_windows_symlink_tag_detected(self):
        self.assertTrue(self._guard_with_tag(_TAG_SYMLINK))

    def test_cloud_placeholder_is_traversable(self):
        # OneDrive placeholders are reparse points but NOT redirects — skipping them
        # would silently stop scanning a redirected Documents folder.
        self.assertFalse(self._guard_with_tag(_TAG_CLOUD))

    def test_no_reparse_tag_is_traversable(self):
        self.assertFalse(self._guard_with_tag(0))


class TestConservativeOnError(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "dir"
        self.d.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lstat_oserror_treated_as_link(self):
        # Can't determine -> don't traverse.
        with patch.object(constants.os, "lstat", side_effect=OSError("boom")):
            self.assertTrue(is_symlink_or_junction(self.d))

    def test_missing_path_treated_as_link(self):
        self.assertTrue(is_symlink_or_junction(self.tmp / "does-not-exist"))


if __name__ == "__main__":
    unittest.main()
