"""Claude Code AGENTS.md discovery (WEB-5938).

Claude Code v2.1.277+ reads AGENTS.md and .claude/AGENTS.md as project
instructions. The rules extractor captured every CLAUDE.md flavour but not
AGENTS.md, so Claude-Code repos using AGENTS.md were under-reported.

The AGENTS.md matcher lives in the shared claude_rules_helpers; the walk is
duplicated per OS, so the capture is exercised against all three OS extractors.

unittest, not pytest: CI runs ``python -m unittest discover -s tests -t .``.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.claude_rules_helpers import is_agents_md_file  # noqa: E402
from coding_discovery_tools.macos.claude_code.claude_rules_extractor import (  # noqa: E402
    MacOSClaudeRulesExtractor,
)
from coding_discovery_tools.linux.claude_code.claude_rules_extractor import (  # noqa: E402
    LinuxClaudeRulesExtractor,
)
from coding_discovery_tools.windows.claude_code.claude_rules_extractor import (  # noqa: E402
    WindowsClaudeRulesExtractor,
)

_EXTRACTORS = (
    ("macos", MacOSClaudeRulesExtractor),
    ("linux", LinuxClaudeRulesExtractor),
    ("windows", WindowsClaudeRulesExtractor),
)


class TestIsAgentsMdFile(unittest.TestCase):
    def test_matches_agents_md_case_insensitive(self):
        self.assertTrue(is_agents_md_file("AGENTS.md"))
        self.assertTrue(is_agents_md_file("agents.md"))
        self.assertTrue(is_agents_md_file("Agents.MD"))

    def test_excludes_local_and_override(self):
        self.assertFalse(is_agents_md_file("AGENTS.local.md"))
        self.assertFalse(is_agents_md_file("AGENTS.override.md"))
        self.assertFalse(is_agents_md_file("agents.local.md"))

    def test_excludes_unrelated_names(self):
        self.assertFalse(is_agents_md_file("README.md"))
        self.assertFalse(is_agents_md_file("MYAGENTS.md"))
        self.assertFalse(is_agents_md_file("AGENTS.txt"))


class TestClaudeCodeAgentsMdCapture(unittest.TestCase):
    """Drives each OS extractor's real ``.github``-style walk over a fixture. The
    fixture lives under the real home so the system-path skip does not drop it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cc-agents-", dir=str(Path.home())))
        # repoA: AGENTS.md AND CLAUDE.md — both must be listed (unconditional capture)
        a = self.root / "repoA"
        a.mkdir()
        (a / "AGENTS.md").write_text("# agents A", encoding="utf-8")
        (a / "CLAUDE.md").write_text("# claude A", encoding="utf-8")
        # repoB: .claude/AGENTS.md
        b = self.root / "repoB" / ".claude"
        b.mkdir(parents=True)
        (b / "AGENTS.md").write_text("# claude-agents B", encoding="utf-8")
        # repoC: variants Claude Code does NOT read
        c = self.root / "repoC"
        c.mkdir()
        (c / "AGENTS.local.md").write_text("# no", encoding="utf-8")
        (c / "AGENTS.override.md").write_text("# no", encoding="utf-8")
        # repoD: everything under .agents/ is ignored by Claude Code
        d = self.root / "repoD" / ".agents"
        d.mkdir(parents=True)
        (d / "AGENTS.md").write_text("# ignored", encoding="utf-8")
        (d / "CLAUDE.md").write_text("# ignored too", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _walk(self, cls):
        projects_by_root = {}
        cls()._walk_for_claude_files(self.root, self.root, projects_by_root, 0)
        return [(Path(root).name, rule)
                for root, rules in projects_by_root.items() for rule in rules]

    def test_agents_md_and_claude_dir_agents_are_captured(self):
        for name, cls in _EXTRACTORS:
            with self.subTest(os=name):
                by = {(root, r["file_name"]): r for root, r in self._walk(cls)}
                self.assertIn(("repoA", "AGENTS.md"), by, "top-level AGENTS.md must be captured")
                self.assertEqual(by[("repoA", "AGENTS.md")]["scope"], "project")
                self.assertIn(("repoB", "AGENTS.md"), by, ".claude/AGENTS.md must be captured")
                self.assertEqual(by[("repoB", "AGENTS.md")]["scope"], "project")
                # Unconditional: CLAUDE.md is still listed alongside AGENTS.md.
                self.assertIn(("repoA", "CLAUDE.md"), by)
                # Shape parity: AGENTS.md carries the same keys as CLAUDE.md.
                self.assertEqual(
                    set(by[("repoA", "AGENTS.md")]), set(by[("repoA", "CLAUDE.md")])
                )

    def test_excluded_variants_and_agents_dir_not_captured(self):
        for name, cls in _EXTRACTORS:
            with self.subTest(os=name):
                rules = self._walk(cls)
                names = {r["file_name"] for _root, r in rules}
                roots = {root for root, _r in rules}
                self.assertNotIn("AGENTS.local.md", names)
                self.assertNotIn("AGENTS.override.md", names)
                # Nothing from under .agents/ (neither its AGENTS.md nor CLAUDE.md).
                self.assertNotIn("repoD", roots, ".agents/ contents must be ignored")


if __name__ == "__main__":
    unittest.main()
