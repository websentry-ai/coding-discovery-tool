"""Regression tests for Codex rules extraction (macOS).

Guards the bug where the project-level walk called
``should_process_directory(dir_path)`` with a single argument, but the helper
signature is ``should_process_directory(directory, root_path)``. That raised a
``TypeError`` on every ``root_path == "/"`` scan (the production path), which was
swallowed by ``extract_all_codex_rules`` into a silent "0 Codex rules".
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.codex.codex_rules_extractor import (
    MacOSCodexRulesExtractor,
)

_CODEX_MOD = "scripts.coding_discovery_tools.macos.codex.codex_rules_extractor"


class TestCodexProjectRulesRootBranch(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCodexRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.proj = Path(self.tmp_dir) / "proj"
        self.proj.mkdir()
        (self.proj / "AGENTS.md").write_text("# project rules\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_root_branch_calls_helper_with_root_and_finds_agents_md(self):
        """The '/' branch must not crash and must reach the AGENTS.md.

        The spy on ``should_process_directory`` (returning True) both lets the
        walk descend deterministically and asserts the call passes BOTH args —
        the one-arg form is exactly the regression.
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
        rules = [r for items in projects_by_root.values() for r in items]
        self.assertTrue(
            any(r.get("file_name") == "AGENTS.md" for r in rules),
            f"AGENTS.md not discovered; rules={rules}",
        )


if __name__ == "__main__":
    unittest.main()
