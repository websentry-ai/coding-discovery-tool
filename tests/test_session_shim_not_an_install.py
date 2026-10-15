"""A per-session wrapper script is not an installation.

Tools that wrap other tools write throwaway shims into the temp directory and
prepend them to PATH, so a ``which`` lookup answers with the wrapper. Recording
one reports an install that never existed, carries no version, and comes back
under a new UUID next session — so the rows accumulate for as long as the device
keeps scanning.
"""
import os
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


class TheDetectorSkipsTheShim(unittest.TestCase):
    """Driven through the detector, not the predicate: the point is that a PATH
    hit on a shim does not become the reported install."""

    def test_a_shim_on_path_is_not_reported_as_the_install(self):
        from scripts.coding_discovery_tools.macos.codex import codex as mod
        shim = f"{TMP}/cmux-cli-shims/{uuid.uuid4()}/codex"
        with patch.object(mod, "run_command", return_value=shim + "\n"), \
                patch.object(mod.Path, "exists", return_value=False):
            self.assertIsNone(mod.MacOSCodexDetector()._check_codex_command())

    def test_a_real_path_hit_is_still_reported(self):
        """The guard must not cost a legitimate PATH detection."""
        from scripts.coding_discovery_tools.macos.codex import codex as mod
        with patch.object(mod, "run_command", return_value="/opt/homebrew/bin/codex\n"), \
                patch.object(mod.Path, "exists", return_value=True):
            self.assertEqual("/opt/homebrew/bin/codex",
                             mod.MacOSCodexDetector()._check_codex_command())


if __name__ == "__main__":
    unittest.main(verbosity=2)
