"""
Regression tests for skill directory paths corrected by a REAL on-machine e2e that
used each tool as its own oracle (its CLI's skill-list, its shipped source, or its
product-bundled docs) — not the vendor docs, which were wrong for several tools.

Each case pins a path the tool ACTUALLY reads that we previously missed (or a
vendor content dir we must still exclude).
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    build_skills_project_list, add_skill_to_project,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file


def _mk(type_dir: Path, name: str):
    d = type_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "Users" / "u"
        self.proj = self.tmp / "proj"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _user(self, extract_user, item_configs):
        us = []
        extract_user(self.home, us, extract_single_rule_file, item_configs)
        return sorted(s["skill_name"] for s in us)

    def _proj(self, extract_dir, parent_dirs, item_configs):
        pbr = {}
        for d in parent_dirs:
            for cfg in item_configs:
                extract_dir(self.proj / d / cfg.dir_name, pbr, extract_single_rule_file,
                            add_skill_to_project, cfg)
        res = build_skills_project_list(pbr)
        self.assertTrue(all(p["project_root"] == str(self.proj) for p in res))
        return sorted(s["skill_name"] for p in res for s in p["skills"])


class TestKiloCodeOracleVerified(_Base):
    """`kilo debug skill` (Kilo's own runtime, an OpenCode fork) discovers more than
    its docs claim: .kilocode (legacy), ~/.config/kilo, and a SINGULAR skill/ dir."""

    def test_user_dirs(self):
        from scripts.coding_discovery_tools.kilocode_skills_helpers import (
            extract_kilocode_user_level_items, KILOCODE_ITEM_CONFIGS)
        _mk(self.home / ".kilo" / "skills", "u_kilo")
        _mk(self.home / ".kilocode" / "skills", "u_kilocode")     # legacy
        _mk(self.home / ".config" / "kilo" / "skills", "u_config")  # nested
        _mk(self.home / ".claude" / "skills", "u_claude")
        names = self._user(extract_kilocode_user_level_items, KILOCODE_ITEM_CONFIGS)
        for n in ("u_kilo", "u_kilocode", "u_config", "u_claude"):
            self.assertIn(n, names)

    def test_config_kilo_resolves_to_home(self):
        from scripts.coding_discovery_tools.kilocode_skills_helpers import (
            extract_kilocode_user_level_items, KILOCODE_ITEM_CONFIGS)
        _mk(self.home / ".config" / "kilo" / "skills", "u_config")
        us = []
        extract_kilocode_user_level_items(self.home, us, extract_single_rule_file, KILOCODE_ITEM_CONFIGS)
        self.assertEqual([s["project_path"] for s in us], [str(self.home)])

    def test_project_dirs_incl_legacy_and_singular(self):
        from scripts.coding_discovery_tools.kilocode_skills_helpers import (
            extract_kilocode_items_from_directory, KILOCODE_PARENT_DIR_NAMES, KILOCODE_ITEM_CONFIGS)
        _mk(self.proj / ".kilo" / "skills", "p_kilo")
        _mk(self.proj / ".kilo" / "skill", "p_singular")          # singular dir
        _mk(self.proj / ".kilocode" / "skills", "p_legacy")
        _mk(self.proj / ".agents" / "skills", "p_agents")
        _mk(self.proj / ".claude" / "skills", "p_claude")
        names = self._proj(extract_kilocode_items_from_directory, KILOCODE_PARENT_DIR_NAMES, KILOCODE_ITEM_CONFIGS)
        self.assertEqual(names, ["p_agents", "p_claude", "p_kilo", "p_legacy", "p_singular"])


class TestWindsurfDevinOracleVerified(_Base):
    """Devin.app's own SKILL.md predicate reads .devin (post-rebrand data folder)
    in addition to .windsurf/.agents/.claude."""

    def test_project_includes_devin(self):
        from scripts.coding_discovery_tools.windsurf_skills_helpers import (
            extract_windsurf_items_from_directory, WINDSURF_PARENT_DIR_NAMES, WINDSURF_ITEM_CONFIGS)
        _mk(self.proj / ".devin" / "skills", "p_devin")
        _mk(self.proj / ".windsurf" / "skills", "p_windsurf")
        _mk(self.proj / ".agents" / "skills", "p_agents")
        _mk(self.proj / ".claude" / "skills", "p_claude")
        self.assertIn(".devin", WINDSURF_PARENT_DIR_NAMES)
        names = self._proj(extract_windsurf_items_from_directory, WINDSURF_PARENT_DIR_NAMES, WINDSURF_ITEM_CONFIGS)
        self.assertEqual(names, ["p_agents", "p_claude", "p_devin", "p_windsurf"])


class TestJunieAgentSkillsOracleVerified(_Base):
    """Junie's product-bundled CLI doc: the CLI reads .junie/agent-skills, while the
    IDE plugin uses .junie/skills. Both must be covered."""

    def test_user_both_dir_names(self):
        from scripts.coding_discovery_tools.junie_skills_helpers import (
            extract_junie_user_level_items, JUNIE_ITEM_CONFIGS)
        _mk(self.home / ".junie" / "skills", "u_ide")
        _mk(self.home / ".junie" / "agent-skills", "u_cli")
        names = self._user(extract_junie_user_level_items, JUNIE_ITEM_CONFIGS)
        self.assertEqual(names, ["u_cli", "u_ide"])

    def test_project_both_dir_names(self):
        from scripts.coding_discovery_tools.junie_skills_helpers import (
            extract_junie_items_from_directory, JUNIE_PARENT_DIR_NAMES, JUNIE_ITEM_CONFIGS)
        _mk(self.proj / ".junie" / "skills", "p_ide")
        _mk(self.proj / ".junie" / "agent-skills", "p_cli")
        names = self._proj(extract_junie_items_from_directory, JUNIE_PARENT_DIR_NAMES, JUNIE_ITEM_CONFIGS)
        self.assertEqual(names, ["p_cli", "p_ide"])


if __name__ == "__main__":
    unittest.main()
