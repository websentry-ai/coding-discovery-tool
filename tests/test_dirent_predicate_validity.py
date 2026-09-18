"""Group 2 - the rule that decided which walkers could be converted.

Path.is_dir() answers False when stat fails; DirEntry.is_dir() raises. Those are
only interchangeable when a raise skips exactly the one entry, which is true when
the try/except sits INSIDE the loop and false when it wraps the whole loop.
"""
import os, shutil, tempfile, unittest
from pathlib import Path


def _symlinks_available():
    """Windows only grants symlink creation to elevated or developer-mode users,
    so the symlink cases are skipped there rather than failing the run."""
    probe = tempfile.mkdtemp()
    try:
        os.symlink(os.path.join(probe, "target"), os.path.join(probe, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


SYMLINKS = _symlinks_available()
needs_symlinks = unittest.skipUnless(SYMLINKS, "symlink creation is not permitted here")


class TestPredicateEquivalence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.d = Path(self.tmp)
        (self.d / "sub").mkdir()
        (self.d / "f.txt").write_text("x")
        if SYMLINKS:
            os.symlink(str(self.d / "sub"), str(self.d / "lnk"))
            os.symlink(str(self.d / "nope"), str(self.d / "broken"))

    def test_predicates_agree_on_every_entry_type(self):
        """dir, file, symlink-to-dir and broken symlink must answer identically."""
        for de in os.scandir(self.d):
            p = Path(de.path)
            self.assertEqual(de.is_dir(), p.is_dir(), f"is_dir differs for {de.name}")
            self.assertEqual(de.is_file(), p.is_file(), f"is_file differs for {de.name}")
            self.assertEqual(de.is_symlink(), p.is_symlink(), f"is_symlink differs for {de.name}")

    @needs_symlinks
    def test_symlink_to_dir_is_followed_by_both(self):
        de = next(e for e in os.scandir(self.d) if e.name == "lnk")
        self.assertTrue(de.is_dir())            # follows, like Path.is_dir()
        self.assertTrue(Path(de.path).is_dir())
        self.assertTrue(de.is_symlink())

    @needs_symlinks
    def test_broken_symlink_is_not_a_dir_for_either(self):
        de = next(e for e in os.scandir(self.d) if e.name == "broken")
        self.assertFalse(de.is_dir())
        self.assertFalse(Path(de.path).is_dir())


class TestRaiseIsEquivalentToSkippingOneEntry(unittest.TestCase):
    """The conversion is safe only where a raising predicate costs one entry."""

    ENTRIES = ["a", "b", "c"]

    def _walk_inner_try(self, predicate):
        """The shape of the 77 converted walkers: try INSIDE the loop."""
        seen = []
        for name in self.ENTRIES:
            try:
                if predicate(name):
                    seen.append(name)
            except (PermissionError, OSError):
                continue
        return seen

    def _walk_outer_try(self, predicate):
        """The shape of the 2 walkers left alone: try AROUND the loop."""
        seen = []
        try:
            for name in self.ENTRIES:
                if predicate(name):
                    seen.append(name)
        except (PermissionError, OSError):
            pass
        return seen

    @staticmethod
    def _path_like(name):
        "Path.is_dir(): swallows the error and answers False."
        return False if name == "b" else True

    @staticmethod
    def _dirent_like(name):
        "DirEntry.is_dir(): propagates the error."
        if name == "b":
            raise PermissionError(13, "denied")
        return True

    def test_inner_try_makes_the_swap_invisible(self):
        """Converted walkers: both shapes skip 'b' and still process 'c'."""
        self.assertEqual(self._walk_inner_try(self._path_like), ["a", "c"])
        self.assertEqual(self._walk_inner_try(self._dirent_like), ["a", "c"])

    def test_outer_try_would_lose_the_rest_of_the_directory(self):
        """The 2 excluded walkers: converting them would drop 'c' entirely."""
        self.assertEqual(self._walk_outer_try(self._path_like), ["a", "c"])
        self.assertEqual(self._walk_outer_try(self._dirent_like), ["a"])  # 'c' lost


class TestExcludedWalkersStillUseIterdir(unittest.TestCase):
    """The two walkers the transform refused must not have been converted."""

    EXCLUDED = [
        ("windows/cursor/settings_extractor.py", "_walk_for_permissions"),
        ("windows/cursor_cli/settings_extractor.py", "_walk_for_cursor_cli_settings"),
    ]

    def test_excluded_walkers_were_left_on_iterdir(self):
        import ast
        pkg = Path(__file__).resolve().parent.parent / "scripts" / "coding_discovery_tools"
        for rel, fname in self.EXCLUDED:
            src = (pkg / rel).read_text()
            tree = ast.parse(src)
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == fname)
            seg = "\n".join(src.split("\n")[fn.lineno - 1:fn.end_lineno])
            self.assertIn(".iterdir()", seg, f"{rel}::{fname} was converted but is unsafe")
            self.assertNotIn("scan_dir_entries", seg, f"{rel}::{fname} must not be converted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
