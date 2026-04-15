"""
Unit tests for Claude Cowork skills extraction.

Covers:
- frontmatter parsing
- skill name resolution priority (frontmatter > H1 > folder)
- deduplication across versioned bundles
- end-to-end macOS extractor (tempdir mirroring the real on-disk shape)
- ephemeral session path exclusion
- claude-code path exclusion (defensive)
- runtime-only skill name exclusion (``context``)
"""

import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.claude_cowork_skills_helpers import (
    COWORK_SESSIONS_DIR,
    SKILLS_PLUGIN_DIR,
    SKILL_FILE_NAME_LOWER,
    RUNTIME_ONLY_NAMES,
    EPHEMERAL_SESSION_PREFIX,
    build_cowork_skill_dict,
    deduplicate_skills,
    extract_skill_name,
    is_claude_code_path,
    is_ephemeral_session_path,
    parse_skill_frontmatter,
    resolve_cowork_scope,
)
from scripts.coding_discovery_tools.macos.claude_cowork import (
    MacOSClaudeCoworkSkillsExtractor,
)


# ─────────────────────────────────────────────────────────────────────────────
# Frontmatter parser
# ─────────────────────────────────────────────────────────────────────────────


class TestParseSkillFrontmatter(unittest.TestCase):
    def test_valid_frontmatter(self):
        content = (
            "---\n"
            "name: my-skill\n"
            'description: "Does things"\n'
            "---\n"
            "# Body\n"
        )
        fm = parse_skill_frontmatter(content)
        self.assertEqual(fm["name"], "my-skill")
        self.assertEqual(fm["description"], "Does things")

    def test_single_quoted_values_are_stripped(self):
        content = "---\nname: 'quoted-skill'\n---\nbody\n"
        fm = parse_skill_frontmatter(content)
        self.assertEqual(fm["name"], "quoted-skill")

    def test_missing_frontmatter(self):
        content = "# Just a heading\n\nsome text"
        self.assertEqual(parse_skill_frontmatter(content), {})

    def test_empty_input(self):
        self.assertEqual(parse_skill_frontmatter(""), {})

    def test_ignores_comments_and_malformed_lines(self):
        content = (
            "---\n"
            "# a comment\n"
            "name: keep-me\n"
            "no-colon-here\n"
            "---\n"
        )
        fm = parse_skill_frontmatter(content)
        self.assertEqual(fm, {"name": "keep-me"})


# ─────────────────────────────────────────────────────────────────────────────
# Skill name resolution
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractSkillName(unittest.TestCase):
    def test_frontmatter_name_wins(self):
        path = Path("/tmp/fake-bundle/skills/folder-name/SKILL.md")
        content = "# H1 Heading\nbody"
        fm = {"name": "frontmatter-name"}
        self.assertEqual(extract_skill_name(path, content, fm), "frontmatter-name")

    def test_h1_fallback(self):
        path = Path("/tmp/fake-bundle/skills/folder-name/SKILL.md")
        content = "some preamble\n# The Real Name\nbody"
        self.assertEqual(extract_skill_name(path, content, {}), "The Real Name")

    def test_folder_fallback(self):
        path = Path("/tmp/fake-bundle/skills/folder-name/SKILL.md")
        content = "no heading here"
        self.assertEqual(extract_skill_name(path, content, {}), "folder-name")

    def test_empty_frontmatter_name_falls_through(self):
        path = Path("/tmp/skills/folder-name/SKILL.md")
        content = "# heading\n"
        fm = {"name": "   "}
        self.assertEqual(extract_skill_name(path, content, fm), "heading")


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────


class TestDeduplicateSkills(unittest.TestCase):
    def test_keeps_newer_copy(self):
        skills = [
            {"skill_name": "schedule", "last_modified": "2025-01-01T00:00:00Z", "file_path": "/a"},
            {"skill_name": "schedule", "last_modified": "2025-06-01T00:00:00Z", "file_path": "/b"},
        ]
        result = deduplicate_skills(skills)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["file_path"], "/b")

    def test_case_insensitive_grouping(self):
        skills = [
            {"skill_name": "Schedule", "last_modified": "2025-01-01T00:00:00Z"},
            {"skill_name": "schedule", "last_modified": "2025-06-01T00:00:00Z"},
        ]
        self.assertEqual(len(deduplicate_skills(skills)), 1)

    def test_skips_entries_without_name(self):
        skills = [
            {"skill_name": "", "last_modified": "2025-01-01T00:00:00Z"},
            {"skill_name": "keep", "last_modified": "2025-01-01T00:00:00Z"},
        ]
        result = deduplicate_skills(skills)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["skill_name"], "keep")

    def test_no_duplicates_passes_through(self):
        skills = [
            {"skill_name": "a", "last_modified": "2025-01-01T00:00:00Z"},
            {"skill_name": "b", "last_modified": "2025-01-01T00:00:00Z"},
        ]
        self.assertEqual(len(deduplicate_skills(skills)), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Path filters
# ─────────────────────────────────────────────────────────────────────────────


class TestPathFilters(unittest.TestCase):
    def test_ephemeral_session_path_detected(self):
        p = Path("/Users/x/Library/Application Support/Claude/local-agent-mode-sessions/local_abc123/skills/s/SKILL.md")
        self.assertTrue(is_ephemeral_session_path(p))

    def test_non_ephemeral_path_not_detected(self):
        p = Path("/Users/x/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/uuid/skills/s/SKILL.md")
        self.assertFalse(is_ephemeral_session_path(p))

    def test_claude_code_path_detected(self):
        p = Path("/Users/x/.claude/skills/mine/SKILL.md")
        self.assertTrue(is_claude_code_path(p))

    def test_non_claude_code_path_not_detected(self):
        p = Path("/Users/x/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/uuid/skills/mine/SKILL.md")
        self.assertFalse(is_claude_code_path(p))


# ─────────────────────────────────────────────────────────────────────────────
# build_cowork_skill_dict
# ─────────────────────────────────────────────────────────────────────────────


def _write_skill(dir_path: Path, body: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    skill_file = dir_path / "SKILL.md"
    skill_file.write_text(body, encoding="utf-8")
    return skill_file


class TestBuildCoworkSkillDict(unittest.TestCase):
    def test_builds_expected_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "my-skill"
            skill_file = _write_skill(
                skill_dir,
                "---\nname: my-skill\ndescription: does a thing\n---\n# My Skill\nbody\n",
            )
            result = build_cowork_skill_dict(skill_file)

        self.assertIsNotNone(result)
        self.assertEqual(result["file_name"], "SKILL.md")
        self.assertEqual(result["scope"], "user")
        self.assertEqual(result["type"], "skill")
        self.assertEqual(result["skill_name"], "my-skill")
        self.assertEqual(result["project_path"], str(Path.home()))
        self.assertFalse(result["truncated"])
        self.assertIn("My Skill", result["content"])

    def test_drops_runtime_only_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "context"
            skill_file = _write_skill(skill_dir, "---\nname: context\n---\nbody\n")
            self.assertIsNone(build_cowork_skill_dict(skill_file))

    def test_uses_folder_name_when_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "folder-named-skill"
            skill_file = _write_skill(skill_dir, "body with no frontmatter or heading")
            result = build_cowork_skill_dict(skill_file)
        self.assertIsNotNone(result)
        self.assertEqual(result["skill_name"], "folder-named-skill")

    def test_tags_ephemeral_session_skill_with_session_ephemeral_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = (
                Path(tmp)
                / f"{EPHEMERAL_SESSION_PREFIX}deadbeef"
                / "skills"
                / "scratchpad"
            )
            skill_file = _write_skill(
                skill_dir,
                "---\nname: scratchpad\n---\n# scratchpad\nbody\n",
            )
            result = build_cowork_skill_dict(skill_file)

        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "session_ephemeral")
        self.assertEqual(result["skill_name"], "scratchpad")

    def test_tags_persistent_skill_with_user_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = (
                Path(tmp)
                / SKILLS_PLUGIN_DIR
                / "bundle-uuid"
                / "version-uuid"
                / "skills"
                / "my-skill"
            )
            skill_file = _write_skill(
                skill_dir,
                "---\nname: my-skill\n---\n# my-skill\nbody\n",
            )
            result = build_cowork_skill_dict(skill_file)

        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "user")


class TestResolveCoworkScope(unittest.TestCase):
    def test_persistent_path_returns_user(self):
        p = Path(
            "/Users/x/Library/Application Support/Claude/local-agent-mode-sessions/"
            "skills-plugin/bundle/version/skills/my-skill/SKILL.md"
        )
        self.assertEqual(resolve_cowork_scope(p), "user")

    def test_ephemeral_path_returns_session_ephemeral(self):
        p = Path(
            "/Users/x/Library/Application Support/Claude/local-agent-mode-sessions/"
            "local_deadbeef/.claude/skills/scratchpad/SKILL.md"
        )
        self.assertEqual(resolve_cowork_scope(p), "session_ephemeral")


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end macOS extractor
# ─────────────────────────────────────────────────────────────────────────────


class TestMacOSClaudeCoworkSkillsExtractor(unittest.TestCase):
    """Walk a tempdir that mirrors the real on-disk shape."""

    def _layout(self, root: Path) -> None:
        """Create a realistic Cowork sessions tree under ``root``."""
        sessions = root / COWORK_SESSIONS_DIR
        plugin = sessions / SKILLS_PLUGIN_DIR

        # Two bundle UUIDs — same "schedule" skill appears in both.
        bundle_a = plugin / "uuid-a" / "uuid-aa" / "skills" / "schedule"
        bundle_a.mkdir(parents=True)
        (bundle_a / "SKILL.md").write_text(
            "---\nname: schedule\n---\n# schedule (old)\n", encoding="utf-8"
        )
        # Manually age the older copy.
        import os
        import time
        old_time = time.time() - (60 * 60 * 24 * 30)  # 30 days ago
        os.utime(bundle_a / "SKILL.md", (old_time, old_time))

        bundle_b = plugin / "uuid-b" / "uuid-bb" / "skills" / "schedule"
        bundle_b.mkdir(parents=True)
        (bundle_b / "SKILL.md").write_text(
            "---\nname: schedule\n---\n# schedule (new)\n", encoding="utf-8"
        )

        # A distinct skill so we get 2 deduped results.
        bundle_c = plugin / "uuid-a" / "uuid-aa" / "skills" / "skill-creator"
        bundle_c.mkdir(parents=True)
        (bundle_c / "SKILL.md").write_text(
            "---\nname: skill-creator\n---\nbody\n", encoding="utf-8"
        )

        # Ephemeral session skill — kept, tagged scope=session_ephemeral.
        ephemeral = sessions / f"{EPHEMERAL_SESSION_PREFIX}deadbeef" / "skills" / "scratchpad"
        ephemeral.mkdir(parents=True)
        (ephemeral / "SKILL.md").write_text(
            "---\nname: scratchpad\n---\nbody\n", encoding="utf-8"
        )

        # Runtime-only skill on disk by name — MUST be skipped by name filter.
        runtime = plugin / "uuid-a" / "uuid-aa" / "skills" / "context"
        runtime.mkdir(parents=True)
        (runtime / "SKILL.md").write_text("---\nname: context\n---\nbody\n", encoding="utf-8")

    def test_end_to_end_dedup_and_scope_tagging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._layout(root)
            extractor = MacOSClaudeCoworkSkillsExtractor(sessions_root=root / COWORK_SESSIONS_DIR)
            result = extractor.extract_all_skills()

        self.assertEqual(result["project_skills"], [])
        names_by_scope = {
            (s["scope"], s["skill_name"]) for s in result["user_skills"]
        }
        self.assertEqual(
            names_by_scope,
            {
                ("user", "schedule"),
                ("user", "skill-creator"),
                ("session_ephemeral", "scratchpad"),
            },
        )

        # Verify dedup kept the newer schedule copy (its content says "new").
        schedule = next(
            s for s in result["user_skills"]
            if s["skill_name"] == "schedule" and s["scope"] == "user"
        )
        self.assertIn("new", schedule["content"])

        # Runtime-only name must still be excluded.
        self.assertNotIn("context", {s["skill_name"] for s in result["user_skills"]})

        # Common envelope fields must be set on every skill.
        for skill in result["user_skills"]:
            self.assertEqual(skill["type"], "skill")
            self.assertEqual(skill["file_name"], "SKILL.md")
            self.assertIn(skill["scope"], {"user", "session_ephemeral"})

    def test_missing_root_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            extractor = MacOSClaudeCoworkSkillsExtractor(sessions_root=missing)
            self.assertEqual(
                extractor.extract_all_skills(),
                {"user_skills": [], "project_skills": []},
            )


if __name__ == "__main__":
    unittest.main()
