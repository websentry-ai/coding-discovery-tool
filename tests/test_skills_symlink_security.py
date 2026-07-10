"""
Security regression: skills walks must NOT follow symlinked directories or files.

A project-planted symlink (e.g. ``.agents -> /Users/victim/.agents``) must not
redirect a privileged/all-user scan into another user's tree and mis-attribute
their skills. Covers four vectors:
  1. symlinked parent dir      (<proj>/.agents -> victim)
  2. symlinked type dir        (<proj>/.agents/skills -> victim)
  3. symlinked skill-name dir  (<proj>/.agents/skills/x -> victim/.../secret)
  4. symlinked SKILL.md file   (<proj>/.agents/skills/x/SKILL.md -> victim file)
Legit (non-symlink) skills in the same tree must still be collected.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.claude_code_skills_helpers import build_skills_project_list


def _skill(dirpath: Path, name: str) -> Path:
    d = dirpath / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")
    return d


class TestSkillsWalkIgnoresSymlinks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Victim tree (another user) with a secret skill.
        self.victim = self.tmp / "Users" / "victim"
        _skill(self.victim / ".agents" / "skills", "secret")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _walk(self, home: Path):
        from scripts.coding_discovery_tools.macos.codex import skills_extractor as se
        ex = se.MacOSCodexSkillsExtractor()
        with patch.object(se, "is_running_as_root", return_value=False), \
             patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            pbr = {}
            ex._walk_for_skills(home, home, pbr, 0)
        return sorted(s["skill_name"] for p in build_skills_project_list(pbr) for s in p["skills"])

    def test_symlinked_parent_dir_not_followed(self):
        home = self.tmp / "Users" / "attacker"
        (home / "proj").mkdir(parents=True)
        os.symlink(self.victim / ".agents", home / "proj" / ".agents")   # .agents -> victim
        _skill(home / "proj2" / ".agents" / "skills", "legit")           # real skill
        names = self._walk(home)
        self.assertIn("legit", names)
        self.assertNotIn("secret", names)

    def test_symlinked_type_dir_not_followed(self):
        home = self.tmp / "Users" / "attacker"
        (home / "proj" / ".agents").mkdir(parents=True)
        os.symlink(self.victim / ".agents" / "skills", home / "proj" / ".agents" / "skills")
        _skill(home / "proj2" / ".agents" / "skills", "legit")
        names = self._walk(home)
        self.assertIn("legit", names)
        self.assertNotIn("secret", names)

    def test_symlinked_skill_name_dir_not_followed(self):
        home = self.tmp / "Users" / "attacker"
        real = home / "proj" / ".agents" / "skills"
        real.mkdir(parents=True)
        os.symlink(self.victim / ".agents" / "skills" / "secret", real / "linked")  # name-dir symlink
        _skill(real, "legit")
        names = self._walk(home)
        self.assertIn("legit", names)
        self.assertNotIn("secret", names)

    def test_symlinked_skill_md_file_not_followed(self):
        home = self.tmp / "Users" / "attacker"
        namedir = home / "proj" / ".agents" / "skills" / "x"
        namedir.mkdir(parents=True)
        os.symlink(self.victim / ".agents" / "skills" / "secret" / "SKILL.md", namedir / "SKILL.md")
        _skill(home / "proj2" / ".agents" / "skills", "legit")
        names = self._walk(home)
        self.assertIn("legit", names)
        # the symlinked SKILL.md must not be ingested (its name-dir "x" yields nothing)
        self.assertEqual(names, ["legit"])


class TestUserLevelDirSymlinkGuard(unittest.TestCase):
    """User-level dirs (~/.agents/skills, ~/.codex/skills, ~/.roo, ...) are opened
    DIRECTLY, not via the guarded walk. A symlinked/junctioned user skills dir — or
    one reached via a symlinked ancestor — must not redirect a root/all-user scan
    into another user's tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.victim = self.tmp / "Users" / "victim"
        _skill(self.victim / ".agents" / "skills", "secret")
        self.attacker = self.tmp / "Users" / "attacker"
        self.attacker.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _extract(self, user_dirs):
        from scripts.coding_discovery_tools.claude_code_skills_helpers import (
            extract_user_level_items, ItemTypeConfig, is_skill_md_file)
        from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file
        cfg = ItemTypeConfig("skill", "skills", "nested", is_skill_md_file, lambda f: f.parent.name)
        us = []
        extract_user_level_items(self.attacker, us, extract_single_rule_file, [cfg],
                                 user_dir_names=user_dirs, parent_dir_names=user_dirs)
        return sorted(s["skill_name"] for s in us)

    def test_symlinked_user_skills_dir_not_followed(self):
        (self.attacker / ".agents").mkdir()
        os.symlink(self.victim / ".agents" / "skills", self.attacker / ".agents" / "skills")
        _skill(self.attacker / ".claude" / "skills", "mine")   # legit control
        names = self._extract((".agents", ".claude"))
        self.assertEqual(names, ["mine"])
        self.assertNotIn("secret", names)

    def test_symlinked_ancestor_not_followed(self):
        # ~/.config -> victim's home; ~/.config/x/skills would escape via the ancestor.
        os.symlink(self.victim, self.attacker / ".config")
        # our "user dir" here is ".config/.agents" style nesting; escape must be rejected
        names = self._extract((".config/.agents",))
        self.assertNotIn("secret", names)


class TestTopLevelDirGuard(unittest.TestCase):
    """The dir that STARTS a walk (drive root child on Windows, top-level dir on
    macOS, user home on Linux) must itself be rejected if it is a symlink/junction —
    otherwise it's walked into before the per-item guard runs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.victim = self.tmp / "Users" / "victim"
        _skill(self.victim / "repo" / ".agents" / "skills", "secret")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_walk_returns_on_symlinked_current_dir(self):
        from scripts.coding_discovery_tools.macos.codex import skills_extractor as se
        home = self.tmp / "Users" / "attacker"
        home.mkdir(parents=True)
        # attacker's top-level dir is a symlink into the victim's tree
        os.symlink(self.victim, home / "linked-top")
        _skill(home / "realrepo" / ".agents" / "skills", "legit")
        ex = se.MacOSCodexSkillsExtractor()
        with patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            pbr = {}
            # simulate the top-level loop: walk each child of home
            for child in home.iterdir():
                ex._walk_for_skills(home, child, pbr, 1)
        names = sorted(s["skill_name"] for p in build_skills_project_list(pbr) for s in p["skills"])
        self.assertEqual(names, ["legit"])   # 'secret' behind the symlinked top dir not reached


class TestContainmentIsIndependentOfLinkGuard(unittest.TestCase):
    """Defence-in-depth (TOCTOU): even if the link check is bypassed — e.g. an
    attacker swaps a dir for a link between the guard and the read — the engine
    re-resolves at extraction time and rejects anything that escaped the skills dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.victim = self.tmp / "Users" / "victim"
        _skill(self.victim / ".agents" / "skills", "secret")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_escaping_skill_md_rejected_even_when_link_guard_says_ok(self):
        from scripts.coding_discovery_tools import claude_code_skills_helpers as engine
        from scripts.coding_discovery_tools.claude_code_skills_helpers import (
            extract_items_from_directory, add_skill_to_project, build_skills_project_list,
        )
        from scripts.coding_discovery_tools.codex_skills_helpers import CODEX_SKILL_CONFIG
        from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file

        type_dir = self.tmp / "proj" / ".agents" / "skills"
        # Escaping skill: real name-dir, but SKILL.md redirects outside type_dir.
        (type_dir / "x").mkdir(parents=True)
        os.symlink(self.victim / ".agents" / "skills" / "secret" / "SKILL.md", type_dir / "x" / "SKILL.md")
        # Control: a fully in-tree skill.
        _skill(type_dir, "legit")

        pbr = {}
        # Simulate the TOCTOU window: pretend the link guard passed for everything.
        with patch.object(engine, "is_symlink_or_junction", return_value=False):
            extract_items_from_directory(
                type_dir, pbr, extract_single_rule_file, add_skill_to_project, CODEX_SKILL_CONFIG,
                parent_dir_names=(".agents",),
            )
        names = sorted(s["skill_name"] for p in build_skills_project_list(pbr) for s in p["skills"])
        self.assertEqual(names, ["legit"])   # "x" (escaping) rejected by containment


if __name__ == "__main__":
    unittest.main()
