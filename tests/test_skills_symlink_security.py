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


if __name__ == "__main__":
    unittest.main()
