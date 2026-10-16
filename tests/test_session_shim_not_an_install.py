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
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.utils import (
    install_from_lookup,
    is_session_temp_path,
)

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
    """The rule reads paths with ``os.path``, so each platform's shape has to be
    checked on that platform.

    A Windows path cannot be judged on POSIX: ``realpath`` treats ``C:\\...`` as a
    relative name and the backslashes as part of one filename. Patching
    ``gettempdir`` to a Windows string does not change that -- it only makes the
    answer depend on whether the checkout itself happens to sit under a temp
    directory, which passes on one machine and fails on the next. So the Windows
    cases run on Windows, where the CI matrix covers them.
    """

    @unittest.skipUnless(os.name == "nt", "Windows path grammar")
    def test_a_windows_temp_shim_is_refused(self):
        shim = (Path(tempfile.gettempdir()) / "cmux-cli-shims"
                / str(uuid.uuid4()) / "codex.exe")
        self.assertTrue(is_session_temp_path(str(shim)))

    @unittest.skipUnless(os.name == "nt", "Windows path grammar")
    def test_a_windows_install_is_kept(self):
        home = Path(os.environ.get("USERPROFILE") or r"C:\Users\dev")
        self.assertFalse(is_session_temp_path(
            str(home / "AppData" / "Roaming" / "npm" / "codex.cmd")))

    @unittest.skipIf(os.name == "nt", "POSIX temp layout")
    def test_every_posix_temp_root_is_covered(self):
        """The macOS per-user layout and the Linux ones answer the same way."""
        for root in ("/tmp", "/var/tmp", "/var/folders/ab/T"):
            with self.subTest(root=root):
                self.assertTrue(is_session_temp_path(
                    f"{root}/cmux-cli-shims/{uuid.uuid4()}/codex"))

    @unittest.skipIf(os.name == "nt", "POSIX temp layout")
    def test_a_posix_install_outside_temp_is_kept(self):
        self.assertFalse(is_session_temp_path("/usr/local/bin/codex"))

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
        # shim or UUID marker, so it is judged on where it really points. What
        # then gets stored is ASymlinkShimIsNotStoredUnderItsTempPath's job.
        self.assertFalse(is_session_temp_path(str(link)))


class TheFilterHandlesWhatWhichReturns(unittest.TestCase):

    def test_a_trailing_newline_is_stripped(self):
        from scripts.coding_discovery_tools.utils import install_from_lookup
        self.assertEqual("/opt/homebrew/bin/codex",
                         install_from_lookup("/opt/homebrew/bin/codex\n"))

    def test_nothing_found_stays_nothing(self):
        from scripts.coding_discovery_tools.utils import install_from_lookup
        for value in (None, "", "   ", "\n"):
            with self.subTest(value=repr(value)):
                self.assertIsNone(install_from_lookup(value))

    def test_a_shim_becomes_nothing_found_when_nothing_else_is_on_path(self):
        shim = f"{TMP}/cmux-cli-shims/{uuid.uuid4()}/codex"
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        # PATH pinned: on a machine that really has the tool the answer is the
        # real install, which AShimDoesNotEndTheSearch covers.
        with patch.dict(os.environ, {"PATH": empty}):
            self.assertIsNone(install_from_lookup(shim + "\n"))


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


@unittest.skipIf(os.name == "nt", "POSIX which backstops; Windows bypasses them")
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

    def _what_answers_without_a_path_hit(self, fn_name, as_root=False):
        """The path this host reports when the lookup finds nothing.

        Not every host can exercise the backstop: a machine-global install
        (``/usr/bin/<tool>`` from a system package) is answered from the
        candidate list before the lookup runs, and is attributed to every user
        by design. Where that happens the backstop never executes, so a case
        built on it would prove nothing and says so instead of asserting.
        """
        return self._run(fn_name, "", as_root=as_root)

    def test_a_shim_on_path_is_never_attributed_to_the_user(self):
        """Unconditional, on every host and privilege level: whatever else the
        scan knows, a shim path may never be the answer."""
        for fn_name, tool in self.BACKSTOPS:
            for as_root in (False, True):
                with self.subTest(backstop=fn_name, root=as_root):
                    found = self._run(
                        fn_name, str(self.shim_dir / tool) + "\n", as_root=as_root)
                    if found is not None:
                        self.assertNotIn(str(self.shim_dir), found)

    def test_a_real_install_on_path_is_still_attributed(self):
        """Proves the backstop is reachable and that refusing shims costs
        nothing. Without this, the case above could pass on a host where the
        lookup never ran at all."""
        reached = 0
        for fn_name, tool in self.BACKSTOPS:
            with self.subTest(backstop=fn_name):
                other = self._what_answers_without_a_path_hit(fn_name)
                if other is not None:
                    self.skipTest(f"answered before the lookup: {other}")
                real = str(self.real_dir / tool)
                self.assertEqual(
                    real, self._run(fn_name, real + "\n", as_root=False))
                reached += 1
        self.assertGreater(reached, 0,
                           "no backstop was reachable here; this host proves nothing")

    def test_under_root_the_lookup_is_skipped_altogether(self):
        """The pre-existing gate: under root ``which`` resolves the scanner's
        PATH, so a hit there -- shim or real -- may not be pinned on a user."""
        for fn_name, tool in self.BACKSTOPS:
            with self.subTest(backstop=fn_name):
                if self._what_answers_without_a_path_hit(fn_name, as_root=True):
                    self.skipTest("a machine-global install answers under root")
                for answer in (str(self.shim_dir / tool), str(self.real_dir / tool)):
                    self.assertIsNone(self._run(fn_name, answer + "\n", as_root=True))


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
        # Anchored to this file, not the working directory: the suite is run
        # from the repo root in CI and from elsewhere by hand, and a relative
        # path would quietly scan nothing and pass.
        root = Path(__file__).resolve().parent.parent / "scripts" / "coding_discovery_tools"
        self.assertTrue(root.is_dir(), f"cannot find the tree at {root}")
        unfiltered = []
        for f in sorted(root.rglob("*.py")):
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'run_command\(\s*\["which"', text):
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.start())
                line = text[start:end if end != -1 else len(text)]
                if "install_from_lookup" not in line:
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
        self.assertNotIn("install_from_lookup", line)



class AShimDoesNotEndTheSearch(unittest.TestCase):
    """``which`` answers with the first PATH hit only. Refusing a shim must not
    turn a real install further along PATH into no install at all -- that trades
    a wrong path for a missing tool, which is the worse of the two.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.shim_dir = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4())
        self.real_dir = Path(self.root) / "custom" / "prefix" / "bin"
        for d in (self.shim_dir, self.real_dir):
            d.mkdir(parents=True)
        for d in (self.shim_dir, self.real_dir):
            f = d / "codex"
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        self.path = os.pathsep.join([str(self.shim_dir), str(self.real_dir)])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_real_install_behind_the_shim_is_reported(self):
        shim = str(self.shim_dir / "codex")
        with patch.dict(os.environ, {"PATH": self.path}):
            self.assertEqual(str(self.real_dir / "codex"),
                             install_from_lookup(shim))

    def test_nothing_is_reported_when_only_the_shim_exists(self):
        """With no real install on PATH there is nothing to recover, and a shim
        is still not an install."""
        shim = str(self.shim_dir / "codex")
        with patch.dict(os.environ, {"PATH": str(self.shim_dir)}):
            self.assertIsNone(install_from_lookup(shim))

    def test_a_second_shim_does_not_stop_the_walk(self):
        other = Path(self.root) / "other-shims" / str(uuid.uuid4())
        other.mkdir(parents=True)
        (other / "codex").write_text("#!/bin/sh\n")
        (other / "codex").chmod(0o755)
        path = os.pathsep.join([str(self.shim_dir), str(other), str(self.real_dir)])
        with patch.dict(os.environ, {"PATH": path}):
            self.assertEqual(str(self.real_dir / "codex"),
                             install_from_lookup(str(self.shim_dir / "codex")))

    @unittest.skipIf(os.name == "nt", "no executable bit on Windows")
    def test_a_non_executable_file_is_not_an_install(self):
        plain = Path(self.root) / "notbin"
        plain.mkdir()
        (plain / "codex").write_text("just a file\n")  # no exec bit
        path = os.pathsep.join([str(self.shim_dir), str(plain)])
        with patch.dict(os.environ, {"PATH": path}):
            self.assertIsNone(install_from_lookup(str(self.shim_dir / "codex")))

    def test_the_detector_reports_the_recovered_install(self):
        """Through the detector, not the helper: the recovered path is what the
        scan ends up carrying."""
        from scripts.coding_discovery_tools.macos.codex import codex as mod
        shim = str(self.shim_dir / "codex")
        with patch.dict(os.environ, {"PATH": self.path}), \
                patch.object(mod, "run_command", return_value=shim + "\n"):
            self.assertEqual(str(self.real_dir / "codex"),
                             mod.MacOSCodexDetector()._check_codex_command())


@unittest.skipIf(os.name == "nt", "the login-shell lookup is POSIX-only")
class TheLoginShellLookupRefusesAShim(unittest.TestCase):
    """The root scan asks each user's login shell for ``command -v <tool>``.
    That is the same kind of lookup as ``which`` and can answer with the same
    shim, so it carries the same rule -- including the recovery, walking the
    user's own PATH rather than the scanner's, which would name a different
    owner for the install.
    """

    def setUp(self):
        from scripts.coding_discovery_tools import utils
        self.utils = utils
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = Path(self.root) / "Users" / "dev"
        self.home.mkdir(parents=True)
        self.shim_dir = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4())
        self.real_dir = Path(self.root) / "custom" / "prefix" / "bin"
        for d in (self.shim_dir, self.real_dir):
            d.mkdir(parents=True)
            f = d / "codex"
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)

    def _resolve(self, answer, user_path):
        class Entry:
            pw_name = "dev"
        stdout = (f"{self.utils._MARKER}PATH\t{user_path}\n"
                  f"{self.utils._MARKER}codex\t{answer}\n")
        completed = types.SimpleNamespace(stdout=stdout, returncode=0)

        def owned(candidate, home):
            # Granted for real install locations only -- never for the shim.
            # A blanket True would also vouch for the shim, and then a check
            # that still described the shim instead of the recovered path
            # would look correct. Compared through realpath, since a temp dir
            # can be handed back in a form realpath expands.
            return (os.path.realpath(str(candidate))
                    .startswith(os.path.realpath(str(self.real_dir))))

        with patch.object(self.utils, "_login_shell_owner", return_value=Entry()), \
                patch.object(self.utils, "_safe_helper", return_value="/usr/bin/sudo"), \
                patch.object(self.utils.subprocess, "run", return_value=completed), \
                patch.object(self.utils, "machine_global_binary_owned_by_user",
                             side_effect=owned):
            return self.utils._resolve_login_shell_tools(self.home)

    def test_a_shim_answer_recovers_the_install_behind_it(self):
        found = self._resolve(
            str(self.shim_dir / "codex"),
            os.pathsep.join([str(self.shim_dir), str(self.real_dir)]))
        self.assertEqual({"codex": str(self.real_dir / "codex")}, found)

    def test_a_shim_with_nothing_behind_it_stores_nothing(self):
        found = self._resolve(str(self.shim_dir / "codex"), str(self.shim_dir))
        self.assertEqual({}, found)

    def test_a_real_answer_is_stored_unchanged(self):
        """Proves the cases above are the rule acting, not the lookup failing."""
        found = self._resolve(str(self.real_dir / "codex"), str(self.real_dir))
        self.assertEqual({"codex": str(self.real_dir / "codex")}, found)

    def test_a_non_executable_candidate_behind_the_shim_is_not_stored(self):
        """The walk requires the executable bit, so a plain file of the right
        name does not become an install."""
        dud_dir = Path(self.root) / "dud" / "bin"
        dud_dir.mkdir(parents=True)
        (dud_dir / "codex").write_text("not executable\n")  # no exec bit
        found = self._resolve(
            str(self.shim_dir / "codex"),
            os.pathsep.join([str(self.shim_dir), str(dud_dir)]))
        self.assertEqual({}, found)

    def test_the_scanners_own_path_is_not_consulted(self):
        """A root scan walks the scanned user's PATH. Falling back to the
        scanner's would pin the scanner's install on that user."""
        scanner_only = Path(self.root) / "scanner" / "bin"
        scanner_only.mkdir(parents=True)
        (scanner_only / "codex").write_text("#!/bin/sh\n")
        (scanner_only / "codex").chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(scanner_only)}):
            found = self._resolve(str(self.shim_dir / "codex"), str(self.shim_dir))
        self.assertEqual({}, found)


class AListingWithSeveralHitsIsReadInOrder(unittest.TestCase):
    """``where`` on Windows prints every match, not just the first, and the
    Windows detectors read that listing. The selector therefore takes a listing
    rather than a single line."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.shim = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4()) / "codex"
        self.real = Path(self.root) / "prefix" / "codex"
        for f in (self.shim, self.real):
            f.parent.mkdir(parents=True)
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)

    def test_the_first_hit_that_is_not_a_shim_wins(self):
        listing = f"{self.shim}\n{self.real}\n"
        self.assertEqual(str(self.real), install_from_lookup(listing))

    def test_order_is_respected_rather_than_preferring_a_later_hit(self):
        listing = f"{self.real}\n{self.shim}\n"
        self.assertEqual(str(self.real), install_from_lookup(listing))

    def test_blank_lines_and_padding_do_not_confuse_it(self):
        listing = f"\n  {self.shim}  \n\n  {self.real} \n\n"
        self.assertEqual(str(self.real), install_from_lookup(listing))

    def test_a_listing_of_only_shims_falls_through_to_path(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        other = Path(self.root) / "more-shims" / str(uuid.uuid4()) / "codex"
        other.parent.mkdir(parents=True)
        other.write_text("#!/bin/sh\n")
        with patch.dict(os.environ, {"PATH": empty}):
            self.assertIsNone(install_from_lookup(f"{self.shim}\n{other}\n"))


class ARelativePathEntryIsNotAnInstall(unittest.TestCase):
    """PATH can hold ``.`` or another relative entry. A match found through one
    would be stored as a path relative to wherever the scan ran, which names a
    different file next time -- and is the same CWD exposure ``_which_no_cwd``
    exists to refuse."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.shim = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4()) / "codex"
        self.shim.parent.mkdir(parents=True)
        self.shim.write_text("#!/bin/sh\n")
        self.shim.chmod(0o755)
        # A planted "codex" reachable only through a relative PATH entry.
        self.cwd_dir = Path(self.root) / "cwd"
        (self.cwd_dir / "bin").mkdir(parents=True)
        plant = self.cwd_dir / "bin" / "codex"
        plant.write_text("#!/bin/sh\n")
        plant.chmod(0o755)

    def test_a_relative_entry_is_skipped(self):
        cwd = os.getcwd()
        os.chdir(self.cwd_dir)
        self.addCleanup(os.chdir, cwd)
        with patch.dict(os.environ,
                        {"PATH": os.pathsep.join([str(self.shim.parent), "bin", "."])}):
            self.assertIsNone(install_from_lookup(str(self.shim)))

    def test_an_absolute_entry_after_it_is_still_used(self):
        """Skipping relative entries must not stop the walk."""
        with patch.dict(os.environ, {"PATH": os.pathsep.join(
                [str(self.shim.parent), "bin", str(self.cwd_dir / "bin")])}):
            self.assertEqual(str(self.cwd_dir / "bin" / "codex"),
                             install_from_lookup(str(self.shim)))


class ASymlinkShimIsNotStoredUnderItsTempPath(unittest.TestCase):
    """A wrapper may plant a symlink to the real binary instead of a script.

    The predicate correctly says that is not a shim -- it resolves outside the
    shim directory -- but the link's own path disappears with the session, so
    storing it would report a different install on every scan. What gets stored
    is the thing it points at.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.real = Path(self.root) / "real" / "codex"
        self.real.parent.mkdir(parents=True)
        self.real.write_text("#!/bin/sh\n")
        self.real.chmod(0o755)
        self.shim_dir = Path(self.root) / "cmux-cli-shims" / str(uuid.uuid4())
        self.shim_dir.mkdir(parents=True)

    def _link(self, target, name="codex"):
        link = self.shim_dir / name
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        return link

    def test_the_target_is_stored_not_the_link(self):
        link = self._link(self.real)
        stored = install_from_lookup(str(link) + "\n")
        self.assertEqual(os.path.realpath(str(self.real)), stored)
        self.assertNotIn("cmux-cli-shims", stored)

    def test_a_wrapper_script_in_the_same_place_is_still_refused(self):
        """The shape actually seen in production is a script, not a link, and it
        must keep being refused rather than resolved to itself."""
        script = self.shim_dir / "gemini"
        script.write_text("#!/bin/sh\nexec true\n")
        script.chmod(0o755)
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        with patch.dict(os.environ, {"PATH": empty}):
            self.assertIsNone(install_from_lookup(str(script) + "\n"))

    def test_a_plain_file_in_temp_is_stored_as_given(self):
        """Only links are followed. Resolving every hit would rewrite the
        recorded path of the fixtures this suite is built on."""
        plain = Path(self.root) / "prefix" / "codex"
        plain.parent.mkdir(parents=True)
        plain.write_text("#!/bin/sh\n")
        plain.chmod(0o755)
        self.assertEqual(str(plain), install_from_lookup(str(plain) + "\n"))

    def test_a_link_outside_temp_is_left_alone(self):
        """The narrow scope is the point: an ordinary install reached through a
        symlink keeps the path it was found at, because that path is part of
        what identifies the row. Following it would make one install arrive
        under two names."""
        try:
            outside = tempfile.mkdtemp(dir=os.path.expanduser("~"))
        except OSError:
            self.skipTest("cannot create a directory outside temp here")
        self.addCleanup(shutil.rmtree, outside, True)
        target = Path(outside) / "cellar" / "codex"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
        link = Path(outside) / "bin" / "codex"
        link.parent.mkdir(parents=True)
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        self.assertEqual(str(link), install_from_lookup(str(link) + "\n"))

    def test_the_recovered_install_is_also_stabilised(self):
        """A shim refused, then a link inside temp found by the walk: the walk's
        answer goes through the same rule."""
        walk_dir = Path(self.root) / "onpath"
        walk_dir.mkdir()
        linked = walk_dir / "opencode"
        os.symlink(self.real, linked)
        script = self.shim_dir / "opencode"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        with patch.dict(os.environ, {"PATH": os.pathsep.join(
                [str(self.shim_dir), str(walk_dir)])}):
            stored = install_from_lookup(str(script) + "\n")
        self.assertEqual(os.path.realpath(str(self.real)), stored)

if __name__ == "__main__":
    unittest.main()
