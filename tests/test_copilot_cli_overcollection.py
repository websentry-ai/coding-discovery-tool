"""Copilot CLI rules/skills over-collection guards (FIX #3).

Two defects, both extraction-only:

* (c) The project walk descended into OTHER tools' per-user config dirs and
  their installed-extension packages (e.g. ``~/.antigravity/extensions/<pkg>/
  .github/instructions/*``), mis-attributing those bundled files to Copilot CLI.
* (b) A repo that symlinks ``AGENTS.md -> CLAUDE.md`` (or keeps byte-identical
  copies) emitted the same instruction content as two rule rows.

NOTE: ``CLAUDE.md`` / ``GEMINI.md`` at a repo root ARE doc-valid Copilot CLI
instruction files (GitHub Copilot CLI custom-instructions reference), so they
are still collected — only the symlink/copy DOUBLE-count is removed.

Walk fixtures live under ``Path.home()`` because the real walk's system-dir skip
treats ``/tmp`` (& ``/var`` / ``/private``) as system paths — matching the
existing ``test_copilot_cli_discovery.py`` convention.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.constants import (
    OTHER_TOOL_CONFIG_DIRS,
    SHARED_SKILL_DIRS,
    traverses_other_tool_config_dir,
)
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_rules_extractor import (
    MacOSCopilotCliRulesExtractor,
)
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_skills_extractor import (
    MacOSCopilotCliSkillsExtractor,
)


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rule_names(projects_by_root: dict) -> dict:
    """Map project_root -> set of collected rule file names."""
    out = {}
    for root, rules in projects_by_root.items():
        out[root] = {r.get("file_name") or Path(r.get("file_path", "")).name for r in rules}
    return out


class TestTraversesOtherToolConfigDir(unittest.TestCase):
    """Pure predicate behind the walk-skip (FIX #3c)."""

    def test_skips_other_tool_dirs(self):
        self.assertTrue(traverses_other_tool_config_dir(Path("/Users/x/.antigravity/extensions/pkg/.github")))
        self.assertTrue(traverses_other_tool_config_dir(Path("/Users/x/.cursor/extensions/p")))
        self.assertTrue(traverses_other_tool_config_dir(Path("/Users/x/.gemini/foo")))

    def test_does_not_skip_a_normal_repo(self):
        self.assertFalse(traverses_other_tool_config_dir(Path("/Users/x/dev/myrepo/.github")))
        self.assertFalse(traverses_other_tool_config_dir(Path("/Users/x/dev/extensions/src")))  # plain dir named "extensions"

    def test_claude_skipped_for_rules_allowed_for_skills(self):
        p = Path("/Users/x/dev/myrepo/.claude/skills/foo")
        self.assertTrue(traverses_other_tool_config_dir(p))  # rules: skip
        self.assertFalse(traverses_other_tool_config_dir(p, allow=SHARED_SKILL_DIRS))  # skills: allow

    def test_claude_is_in_the_set(self):
        self.assertIn(".claude", OTHER_TOOL_CONFIG_DIRS)
        self.assertIn(".antigravity", OTHER_TOOL_CONFIG_DIRS)


class TestRulesWalkSkipsOtherToolDirs(unittest.TestCase):
    """The real rules walk collects a genuine repo but not extension packages."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliRulesExtractor()
        self.root = Path(tempfile.mkdtemp(dir=str(Path.home())))
        # A genuine repo: collect AGENTS.md + .github/copilot-instructions.md.
        self.repo = self.root / "realrepo"
        _write(self.repo / ".git" / "HEAD", "ref: refs/heads/main")
        _write(self.repo / "AGENTS.md", "# agents")
        _write(self.repo / ".github" / "copilot-instructions.md", "# repo instructions")
        # An installed extension package under another tool's config dir: NOT collected.
        ext = self.root / ".antigravity" / "extensions" / "ms-python-1.0" / ".github"
        _write(ext / "copilot-instructions.md", "# ext instructions")
        _write(ext / "instructions" / "x.instructions.md", "# ext path instr")
        ext_repo = self.root / ".cursor" / "extensions" / "pkg"
        _write(ext_repo / ".git" / "HEAD", "ref: refs/heads/main")
        _write(ext_repo / "AGENTS.md", "# cursor ext agents")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_real_repo_collected_extension_dirs_skipped(self):
        projects_by_root = {}
        self.extractor._walk_for_project_rules(self.root, self.root, projects_by_root, 0)
        names = _rule_names(projects_by_root)

        self.assertIn(str(self.repo), names)
        self.assertEqual(names[str(self.repo)], {"AGENTS.md", "copilot-instructions.md"})
        # Nothing from another tool's config dir / extension package.
        leaked = [root for root in names if ".antigravity" in root or ".cursor" in root]
        self.assertEqual(leaked, [], f"leaked extension-dir rules: {leaked}")


class TestSkillsWalkSkipsOtherToolDirs(unittest.TestCase):
    """The skills walk keeps shared .claude/.agents repo skills but skips extensions."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliSkillsExtractor()
        self.root = Path(tempfile.mkdtemp(dir=str(Path.home())))
        self.repo = self.root / "realrepo"
        _write(self.repo / ".github" / "skills" / "foo" / "SKILL.md", "# foo skill")
        _write(self.repo / ".claude" / "skills" / "bar" / "SKILL.md", "# bar skill")  # shared convention
        # Extension package's bundled skills under another tool's config dir: NOT collected.
        _write(self.root / ".antigravity" / "extensions" / "pkg" / ".github" / "skills" / "baz" / "SKILL.md", "# baz")
        _write(self.root / ".cursor" / "extensions" / "pkg" / ".claude" / "skills" / "qux" / "SKILL.md", "# qux")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_shared_repo_skills_collected_extension_skills_skipped(self):
        projects_by_root = {}
        self.extractor._walk_for_skills(self.root, self.root, projects_by_root, 0)

        all_paths = [
            s.get("file_path", "")
            for skills in projects_by_root.values()
            for s in skills
        ]
        names = {Path(p).parent.name for p in all_paths}  # skill dir names
        # The genuine repo's .github AND .claude skills are collected.
        self.assertIn("foo", names)
        self.assertIn("bar", names)
        # Nothing from extension packages under other-tool config dirs.
        self.assertNotIn("baz", names)
        self.assertNotIn("qux", names)
        self.assertFalse(
            any(".antigravity" in p or ".cursor" in p for p in all_paths),
            f"leaked extension skills: {all_paths}",
        )


class TestRootRuleSymlinkAndCopyDedup(unittest.TestCase):
    """FIX #3b: AGENTS.md symlinked to / copied as CLAUDE.md emits once."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliRulesExtractor()
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _repo(self, name: str) -> Path:
        repo = self.root / name
        (repo / ".git").mkdir(parents=True)
        return repo

    def test_symlinked_agents_claude_emits_once(self):
        repo = self._repo("symrepo")
        _write(repo / "AGENTS.md", "# shared instructions\n")
        os.symlink(repo / "AGENTS.md", repo / "CLAUDE.md")
        pbr = {}
        self.extractor._extract_project_root_files(repo, pbr)
        self.assertEqual(len(pbr.get(str(repo), [])), 1)

    def test_byte_identical_copies_emit_once(self):
        repo = self._repo("copyrepo")
        _write(repo / "AGENTS.md", "# identical body\n")
        _write(repo / "CLAUDE.md", "# identical body\n")
        pbr = {}
        self.extractor._extract_project_root_files(repo, pbr)
        self.assertEqual(len(pbr.get(str(repo), [])), 1)

    def test_distinct_content_emits_both(self):
        repo = self._repo("distinctrepo")
        _write(repo / "AGENTS.md", "# agents body\n")
        _write(repo / "CLAUDE.md", "# claude body\n")
        pbr = {}
        self.extractor._extract_project_root_files(repo, pbr)
        names = {r.get("file_name") or Path(r["file_path"]).name for r in pbr.get(str(repo), [])}
        self.assertEqual(names, {"AGENTS.md", "CLAUDE.md"})

    def test_no_git_dir_collects_nothing(self):
        repo = self.root / "notarepo"
        _write(repo / "AGENTS.md", "# x\n")
        pbr = {}
        self.extractor._extract_project_root_files(repo, pbr)
        self.assertEqual(pbr, {})


if __name__ == "__main__":
    unittest.main()
