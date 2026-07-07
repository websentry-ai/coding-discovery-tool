"""
Over-collection guard tests for the newly-added skills extractors.

A tool's project walk must NOT descend into ANOTHER tool's ``~/.<tool>/`` config
dir (e.g. ``~/.antigravity/extensions/<pkg>``) and collect its bundled
``.agents``/``.claude`` skills as if they were the user's project skills. This is
enforced by ``traverses_other_tool_config_dir(item, allow=...)`` in each walk,
where ``allow`` = SHARED_SKILL_DIRS | the tool's own parent dirs (so the tool's
OWN dirs are never skipped).
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.claude_code_skills_helpers import build_skills_project_list


def _mk_skill(type_dir: Path, name: str):
    d = type_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")


def _walk(se_module, extractor_cls, home: Path):
    ex = extractor_cls()
    with patch.object(se_module, "is_running_as_root", return_value=False), \
         patch.object(se_module, "should_skip_system_path", return_value=False), \
         patch("pathlib.Path.home", return_value=home):
        pbr = {}
        ex._walk_for_skills(home, home, pbr, 0)
    return sorted(s["skill_name"] for pr in build_skills_project_list(pbr) for s in pr["skills"])


class TestCodexOverCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundled_agents_skill_in_other_tool_dir_not_collected(self):
        from scripts.coding_discovery_tools.macos.codex import skills_extractor as se
        home = self.tmp / "Users" / "alice"
        # Bundled inside ANOTHER tool's config dir -> must be skipped
        _mk_skill(home / ".antigravity" / "extensions" / "pkg" / ".agents" / "skills", "bundled")
        _mk_skill(home / ".cursor" / "cached" / ".agents" / "skills", "cached")
        # A legit project skill -> must be collected
        _mk_skill(home / "proj" / ".agents" / "skills", "legit")
        names = _walk(se, se.MacOSCodexSkillsExtractor, home)
        self.assertEqual(names, ["legit"])
        self.assertNotIn("bundled", names)
        self.assertNotIn("cached", names)


class TestGeminiOverCollectionKeepsOwnDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_own_gemini_dir_collected_but_other_tool_dir_skipped(self):
        # .gemini is itself in OTHER_TOOL_CONFIG_DIRS — the per-tool allow set must
        # keep Gemini's OWN .gemini/skills collectable while skipping other tools' dirs.
        from scripts.coding_discovery_tools.macos.gemini_cli import skills_extractor as se
        home = self.tmp / "Users" / "bob"
        _mk_skill(home / "proj" / ".gemini" / "skills", "own")          # own dir -> collected
        _mk_skill(home / "proj" / ".agents" / "skills", "shared")       # shared alias -> collected
        _mk_skill(home / ".roo" / "bundled" / ".agents" / "skills", "roobundle")  # other tool -> skipped
        names = _walk(se, se.MacOSGeminiCliSkillsExtractor, home)
        self.assertEqual(names, ["own", "shared"])
        self.assertNotIn("roobundle", names)


if __name__ == "__main__":
    unittest.main()
