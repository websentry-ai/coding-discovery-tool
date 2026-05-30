"""
Unit tests for Claude Cowork skills extraction on Linux.

Mirrors tests/test_cowork_skills_extraction.py (macOS) but exercises the
Linux extractor (LinuxClaudeCoworkSkillsExtractor). Unlike the macOS
extractor, the Linux one deduplicates the explicit-sessions-root path
unconditionally, so this test is correct whether or not it runs as root
(important for root CI/VM environments).

Covers:
- end-to-end Linux extractor (tempdir mirroring the real on-disk shape)
- deduplication across versioned bundles (keeps newest by mtime)
- ephemeral session path skipping
- runtime-only skill name exclusion (``context``)
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.claude_cowork_skills_helpers import (
    COWORK_SESSIONS_DIR,
    SKILLS_PLUGIN_DIR,
    EPHEMERAL_SESSION_PREFIX,
)
from scripts.coding_discovery_tools.linux.claude_cowork import (
    LinuxClaudeCoworkSkillsExtractor,
)


class TestLinuxClaudeCoworkSkillsExtractor(unittest.TestCase):
    """Walk a tempdir that mirrors the real on-disk shape on Linux."""

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

        # Ephemeral session skill — must be skipped entirely.
        ephemeral = sessions / f"{EPHEMERAL_SESSION_PREFIX}deadbeef" / ".claude" / "skills" / "scratchpad"
        ephemeral.mkdir(parents=True)
        (ephemeral / "SKILL.md").write_text(
            "---\nname: scratchpad\n---\nbody\n", encoding="utf-8"
        )

        # Runtime-only skill on disk by name — MUST be skipped by name filter.
        runtime = plugin / "uuid-a" / "uuid-aa" / "skills" / "context"
        runtime.mkdir(parents=True)
        (runtime / "SKILL.md").write_text("---\nname: context\n---\nbody\n", encoding="utf-8")

    def test_end_to_end_dedup_and_ephemeral_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._layout(root)
            extractor = LinuxClaudeCoworkSkillsExtractor(sessions_root=root / COWORK_SESSIONS_DIR)
            result = extractor.extract_all_skills()

        self.assertEqual(result["project_skills"], [])
        skill_names = {s["skill_name"] for s in result["user_skills"]}
        self.assertEqual(skill_names, {"schedule", "skill-creator"})

        # Exactly one "schedule" entry after dedup (no duplicate bundles).
        schedules = [s for s in result["user_skills"] if s["skill_name"] == "schedule"]
        self.assertEqual(len(schedules), 1)

        # Verify dedup kept the newer schedule copy (its content says "new").
        self.assertIn("new", schedules[0]["content"])
        self.assertNotIn("old", schedules[0]["content"])

        # Ephemeral session skill must be skipped.
        self.assertNotIn("scratchpad", skill_names)

        # Runtime-only name must still be excluded.
        self.assertNotIn("context", skill_names)

        # Common envelope fields must be set on every skill.
        for skill in result["user_skills"]:
            self.assertEqual(skill["type"], "skill")
            self.assertEqual(skill["file_name"], "SKILL.md")
            self.assertEqual(skill["scope"], "user")

    def test_no_sessions_dir_returns_empty(self):
        """A non-existent sessions tree yields no skills, no error."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist" / COWORK_SESSIONS_DIR
            extractor = LinuxClaudeCoworkSkillsExtractor(sessions_root=missing)
            result = extractor.extract_all_skills()
        self.assertEqual(result, {"user_skills": [], "project_skills": []})


if __name__ == "__main__":
    unittest.main()
