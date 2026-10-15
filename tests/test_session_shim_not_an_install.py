"""A per-session wrapper script is not an installation.

Tools that wrap other tools write throwaway shims into the temp directory and
prepend them to PATH, so a ``which`` lookup answers with the wrapper. Recording
one reports an install that never existed, carries no version, and comes back
under a new UUID next session — so the rows accumulate for as long as the device
keeps scanning.
"""
import importlib
import os
import re
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.utils import is_session_temp_path

TMP = tempfile.gettempdir()


class ASessionShimIsRefused(unittest.TestCase):

    def test_the_shape_seen_in_production_is_refused(self):
        path = f"{TMP}/cmux-cli-shims/{uuid.uuid4()}/codex"
        self.assertTrue(is_session_temp_path(path))

    def test_a_uuid_named_parent_in_temp_is_refused(self):
        """The directory name is machine-generated per session, whoever wrote
        it — so the rule does not have to know about any particular tool."""
        self.assertTrue(is_session_temp_path(f"{TMP}/{uuid.uuid4()}/codex"))

    def test_a_shim_directory_anywhere_in_the_path_is_refused(self):
        self.assertTrue(is_session_temp_path(f"{TMP}/some-tool-shims/x/gemini"))


class AnOrdinaryInstallIsKept(unittest.TestCase):

    def test_the_common_install_locations_are_kept(self):
        for path in ("/opt/homebrew/bin/codex",      # the most common on macOS
                     "/usr/local/bin/codex",
                     os.path.expanduser("~/.local/bin/codex"),
                     "/usr/bin/git"):
            with self.subTest(path=path):
                self.assertFalse(is_session_temp_path(path))

    def test_a_test_fixture_rooted_in_temp_is_kept(self):
        """This repo roots synthetic filesystems at ``mkdtemp()``, so being under
        a temp directory cannot by itself mean 'not an install' — otherwise every
        fixture path in the suite would be refused."""
        for path in (f"{TMP}/tmpabc123/home/alice/.junie",
                     f"{TMP}/tmpabc123/custom/junie",
                     f"{TMP}/tmpabc123/Users/alice/.opencode/bin/opencode"):
            with self.subTest(path=path):
                self.assertFalse(is_session_temp_path(path))

    def test_junk_input_does_not_raise(self):
        for value in (None, "", 0, [], {}, Path("/opt/homebrew/bin/codex")):
            with self.subTest(value=repr(value)):
                is_session_temp_path(value)


class TheRuleHoldsOnEveryPlatform(unittest.TestCase):

    def test_a_windows_temp_shim_is_refused(self):
        """Windows has no /var/folders; the rule leans on gettempdir() there, so
        the shape has to be checked on its own."""
        with patch("tempfile.gettempdir",
                   return_value=r"C:\Users\dev\AppData\Local\Temp"):
            self.assertTrue(is_session_temp_path(
                r"C:\Users\dev\AppData\Local\Temp\cmux-cli-shims\abc\codex"))

    def test_a_windows_install_is_kept(self):
        with patch("tempfile.gettempdir",
                   return_value=r"C:\Users\dev\AppData\Local\Temp"):
            self.assertFalse(is_session_temp_path(
                r"C:\Users\dev\AppData\Roaming\npm\codex.cmd"))

    def test_a_symlink_out_of_temp_follows_to_the_real_file(self):
        """A shim directory holding a symlink to the real binary is not a shim
        install: realpath resolves it, and the real location decides."""
        real_dir = tempfile.mkdtemp()  # stands in for an install location
        real = Path(real_dir) / "codex"
        real.write_text("#!/bin/sh\n")
        link_parent = Path(tempfile.mkdtemp()) / "cmux-cli-shims" / str(uuid.uuid4())
        link_parent.mkdir(parents=True)
        link = link_parent / "codex"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        # The link lives in a shim dir but resolves to a plain temp path with no
        # shim or UUID marker, so it is judged on where it really points.
        self.assertFalse(is_session_temp_path(str(link)))


class TheFilterHandlesWhatWhichReturns(unittest.TestCase):

    def test_a_trailing_newline_is_stripped(self):
        from scripts.coding_discovery_tools.utils import not_session_shim
        self.assertEqual("/opt/homebrew/bin/codex",
                         not_session_shim("/opt/homebrew/bin/codex\n"))

    def test_nothing_found_stays_nothing(self):
        from scripts.coding_discovery_tools.utils import not_session_shim
        for value in (None, "", "   ", "\n"):
            with self.subTest(value=repr(value)):
                self.assertIsNone(not_session_shim(value))

    def test_a_shim_becomes_nothing_found(self):
        from scripts.coding_discovery_tools.utils import not_session_shim
        shim = f"{TMP}/cmux-cli-shims/{uuid.uuid4()}/codex"
        self.assertIsNone(not_session_shim(shim + "\n"))


class TheDetectorSkipsTheShim(unittest.TestCase):
    """Driven through the detector, not the predicate: the point is that a PATH
    hit on a shim does not become the reported install.

    Both files are planted for real. Patching ``Path.exists`` to ``False``
    instead would let the detector's own existence check refuse the shim, and
    the test would pass whether or not the rule is there at all.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.shim = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4()) / "codex"
        self.real = Path(self.root) / "opt" / "homebrew" / "bin" / "codex"
        for f in (self.shim, self.real):
            f.parent.mkdir(parents=True)
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _detect(self, which_answers):
        from scripts.coding_discovery_tools.macos.codex import codex as mod
        with patch.object(mod, "run_command", return_value=which_answers):
            return mod.MacOSCodexDetector()._check_codex_command()

    def test_a_shim_on_path_is_not_reported_as_the_install(self):
        """Not asserted as ``None``: with the shim refused the detector falls
        through to its candidate list, which on a machine that really has the
        tool answers with the real path. What must never come back is the shim."""
        found = self._detect(str(self.shim) + "\n")
        self.assertNotEqual(str(self.shim), found)
        if found:
            self.assertNotIn("cmux-cli-shims", found)

    def test_a_real_path_hit_is_still_reported(self):
        """The guard must not cost a legitimate PATH detection."""
        self.assertEqual(str(self.real), self._detect(str(self.real) + "\n"))


class TheRootScanOverTheUserHomesRefusesAShim(unittest.TestCase):
    """``user_tool_detector`` is the surface that runs under MDM: one scan walks
    every home on the box. Its three PATH backstops were filtered too, and they
    sit behind pre-existing root gates -- so both the gate and the new rule are
    checked here, since a gate that already returns ``None`` would otherwise
    make the rule look effective when it never ran.
    """

    BACKSTOPS = [
        ("find_junie_binary_for_user", "junie"),
        ("find_claude_binary_for_user", "claude"),
        ("find_cursor_agent_binary_for_user", "cursor-agent"),
    ]

    def setUp(self):
        from scripts.coding_discovery_tools import user_tool_detector
        self.mod = user_tool_detector
        self.root = tempfile.mkdtemp()
        # An empty home, so nothing in the explicit candidate list matches and
        # the lookup under test is actually the one that answers.
        self.home = Path(self.root) / "Users" / "dev"
        self.home.mkdir(parents=True)
        self.shim_dir = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4())
        self.real_dir = Path(self.root) / "opt" / "bin"
        for d in (self.shim_dir, self.real_dir):
            d.mkdir(parents=True)
        for _, tool in self.BACKSTOPS:
            for d in (self.shim_dir, self.real_dir):
                f = d / tool
                f.write_text("#!/bin/sh\n")
                f.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, fn_name, lookup_answer, as_root):
        with patch.object(self.mod, "run_command", return_value=lookup_answer), \
             patch.object(self.mod, "is_running_as_root", return_value=as_root), \
             patch.object(self.mod, "_is_scanning_users_own_home",
                          return_value=not as_root), \
             patch.object(self.mod, "resolve_npm_global_tool_bin",
                          return_value=None), \
             patch.object(self.mod, "user_login_shell_tool_path",
                          return_value=None):
            return getattr(self.mod, fn_name)(self.home)

    def test_a_shim_on_path_is_not_attributed_to_the_user(self):
        for fn_name, tool in self.BACKSTOPS:
            with self.subTest(backstop=fn_name):
                shim = str(self.shim_dir / tool)
                self.assertIsNone(self._run(fn_name, shim + "\n", as_root=False))

    def test_a_real_install_on_path_is_still_attributed(self):
        """Proves the backstop is reachable in this setup -- without this the
        test above would pass on a gate that never ran the rule."""
        for fn_name, tool in self.BACKSTOPS:
            with self.subTest(backstop=fn_name):
                real = str(self.real_dir / tool)
                self.assertEqual(
                    real, self._run(fn_name, real + "\n", as_root=False))

    def test_under_root_the_lookup_is_skipped_altogether(self):
        """The pre-existing gate: under root ``which`` resolves the scanner's
        PATH, so neither a shim nor a real hit may be attributed to a user."""
        for fn_name, tool in self.BACKSTOPS:
            with self.subTest(backstop=fn_name):
                for answer in (str(self.shim_dir / tool),
                               str(self.real_dir / tool)):
                    self.assertIsNone(
                        self._run(fn_name, answer + "\n", as_root=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class EveryToolThatResolvesOnPathRefusesAShim(unittest.TestCase):
    """The filter went in at every ``which`` lookup in the tree. One detector
    behaving says nothing about the others, so each one is driven here with a
    shim planted on disk and returned by its own lookup.

    The files are real: several detectors confirm the path exists before
    reporting it, so a non-existent shim would be refused for the wrong reason
    and the test would pass with or without the rule.
    """

    # (module, class, method, the name the detector looks up)
    DETECTORS = [
        ("macos.codex.codex", "MacOSCodexDetector", "_check_codex_command", "codex"),
        ("macos.gemini_cli.gemini_cli", "MacOSGeminiCliDetector", "_check_gemini_command", "gemini"),
        ("macos.cursor_cli.cursor_cli", "MacOSCursorCliDetector", "_check_cursor_command", "cursor-agent"),
        ("macos.opencode.opencode", "MacOSOpenCodeDetector", "_check_opencode_command", "opencode"),
        ("linux.codex.codex", "LinuxCodexDetector", "_check_codex_command", "codex"),
        ("linux.cursor_cli.cursor_cli", "LinuxCursorCliDetector", "_check_cursor_command", "cursor-agent"),
        ("linux.gemini_cli.gemini_cli", "LinuxGeminiCliDetector", "_check_gemini_command", "gemini"),
        ("linux.opencode.opencode", "LinuxOpenCodeDetector", "_check_opencode_command", "opencode"),
        ("linux.cursor.cursor", "LinuxCursorDetector", "detect", "cursor"),
        ("linux.windsurf.windsurf", "LinuxWindsurfDetector", "detect", "windsurf"),
    ]

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.shim_dir = Path(cls.root) / "cmux-cli-shims" / str(uuid.uuid4())
        cls.real_dir = Path(cls.root) / "usr" / "local" / "bin"
        for d in (cls.shim_dir, cls.real_dir):
            d.mkdir(parents=True, exist_ok=True)
        for _, _, _, tool in cls.DETECTORS:
            for d in (cls.shim_dir, cls.real_dir):
                f = d / tool
                f.write_text("#!/bin/sh\nexec \"$@\"\n")
                f.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _load(self, suffix):
        return importlib.import_module(
            "scripts.coding_discovery_tools." + suffix)

    def _install_path(self, result):
        """Detectors answer with a path; ``detect`` answers with a record."""
        if isinstance(result, dict):
            return result.get("install_path")
        return result

    def test_no_detector_reports_the_shim(self):
        for suffix, cls_name, method, tool in self.DETECTORS:
            with self.subTest(detector=cls_name, tool=tool):
                mod = self._load(suffix)
                shim = str(self.shim_dir / tool)
                with patch.object(mod, "run_command", return_value=shim + "\n"):
                    found = self._install_path(
                        getattr(getattr(mod, cls_name)(), method)())
                self.assertNotEqual(shim, found)
                # Nor any other file the shim directory happens to hold.
                if found:
                    self.assertFalse(str(self.shim_dir) in found,
                                     f"{cls_name} reported {found}")

    def test_no_detector_loses_a_real_install(self):
        """The other half of the rule: refusing shims must not cost real hits."""
        for suffix, cls_name, method, tool in self.DETECTORS:
            with self.subTest(detector=cls_name, tool=tool):
                mod = self._load(suffix)
                real = str(self.real_dir / tool)
                with patch.object(mod, "run_command", return_value=real + "\n"):
                    found = self._install_path(
                        getattr(getattr(mod, cls_name)(), method)())
                self.assertEqual(real, found)


class NoLookupBypassesTheFilter(unittest.TestCase):
    """Structural backstop for the call sites this machine cannot execute, and
    for detectors added later: every ``which`` lookup in the tree is filtered."""

    def test_every_which_lookup_is_filtered(self):
        root = Path("scripts/coding_discovery_tools")
        unfiltered = []
        for f in sorted(root.rglob("*.py")):
            text = f.read_text()
            for m in re.finditer(r'run_command\(\s*\["which"', text):
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.start())
                line = text[start:end if end != -1 else len(text)]
                if "not_session_shim" not in line:
                    unfiltered.append(
                        f"{f}:{text.count(chr(10), 0, m.start()) + 1}")
        self.assertEqual([], unfiltered,
                         "these lookups can still report a shim")

    def test_the_backstop_would_notice_a_bypass(self):
        """A check that cannot fail guards nothing, and this one reads the tree
        rather than the diff -- so it is exercised against a known bypass."""
        import re as _re
        line = '            output = run_command(["which", "codex"], VERSION_TIMEOUT)'
        self.assertTrue(_re.search(r'run_command\(\s*\["which"', line))
        self.assertNotIn("not_session_shim", line)


if __name__ == "__main__":
    unittest.main()
