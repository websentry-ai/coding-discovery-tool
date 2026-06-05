"""Regression tests for Codex rules extraction (macOS).

Guards the bug where the project-level walk called
``should_process_directory(dir_path)`` with a single argument, but the helper
signature is ``should_process_directory(directory, root_path)``. That raised a
``TypeError`` on every ``root_path == "/"`` scan (the production path), which was
swallowed by ``extract_all_codex_rules`` into a silent "0 Codex rules".
"""
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools import macos_extraction_helpers as mac_helpers
from scripts.coding_discovery_tools.macos.codex.codex_rules_extractor import (
    MacOSCodexRulesExtractor,
)

_CODEX_MOD = "scripts.coding_discovery_tools.macos.codex.codex_rules_extractor"


class TestCodexProjectRulesRootBranch(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCodexRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.root = Path(self.tmp_dir)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        (self.proj / "AGENTS.md").write_text("# project rules\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_root_branch_passes_root_path_to_should_process_directory(self):
        """The '/' branch must call ``should_process_directory(directory, root_path)``
        — the one-arg form was the regression. The spy records the call args to
        pin that contract (cross-platform).

        The AGENTS.md file-finding assertion is macOS/Linux-only: the production
        '/' scan is POSIX-only, and on Windows a ``C:\\`` temp path cannot be made
        relative to ``/`` (the walk's ``relative_to`` raises ``ValueError`` and
        skips items), so it is not asserted there.
        """
        projects_by_root: dict = {}
        seen_args = []

        def spy(*args):
            seen_args.append(args)
            return True

        with patch(f"{_CODEX_MOD}.get_top_level_directories", return_value=[self.proj]), \
             patch(f"{_CODEX_MOD}.should_process_directory", side_effect=spy), \
             patch(f"{_CODEX_MOD}.should_skip_system_path", return_value=False):
            self.extractor._extract_project_level_rules(Path("/"), projects_by_root)

        self.assertTrue(seen_args, "should_process_directory was never called")
        self.assertTrue(
            all(len(a) == 2 for a in seen_args),
            f"should_process_directory must be called with (directory, root_path); got {seen_args}",
        )
        if platform.system() != "Windows":
            rules = [r for items in projects_by_root.values() for r in items]
            self.assertTrue(
                any(r.get("file_name") == "AGENTS.md" for r in rules),
                f"AGENTS.md not discovered; rules={rules}",
            )

    def test_real_should_process_directory_requires_root_path(self):
        """Exercise the REAL helper: the one-arg call (the production bug) raises
        TypeError; the two-arg form returns a bool. Cross-platform, no '/' walk."""
        with self.assertRaises(TypeError):
            mac_helpers.should_process_directory(self.proj)  # 1 arg = the regression
        self.assertIsInstance(
            mac_helpers.should_process_directory(self.proj, self.root), bool
        )


if __name__ == "__main__":
    unittest.main()
