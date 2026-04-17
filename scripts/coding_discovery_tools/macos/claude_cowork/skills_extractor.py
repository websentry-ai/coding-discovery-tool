"""
Claude Cowork skills extraction for macOS.

Walks Claude Desktop's on-disk session tree looking for SKILL.md files.
When running as root (MDM deployments), walks every user's sessions tree
via scan_user_directories. Otherwise walks only the current user's tree.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseClaudeCoworkSkillsExtractor
from ...claude_cowork_skills_helpers import (
    COWORK_SESSIONS_DIR,
    SKILL_FILE_NAME_LOWER,
    build_cowork_skill_dict,
    deduplicate_skills,
    is_claude_code_path,
    is_ephemeral_session_path,
)
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


def _sessions_dir_for_user(user_home: Path) -> Path:
    """Cowork sessions tree for a specific macOS user."""
    return (
        user_home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
    )


class MacOSClaudeCoworkSkillsExtractor(BaseClaudeCoworkSkillsExtractor):
    """Extractor for Claude Cowork skills on macOS."""

    def __init__(self, sessions_root: Optional[Path] = None):
        # Overridable so tests can point at a tempdir.
        self._explicit_sessions_root = sessions_root

    def extract_all_skills(self) -> Dict:
        collected: List[Dict] = []

        if self._explicit_sessions_root is not None:
            # Explicit root (tests).
            self._collect_from(self._explicit_sessions_root, Path.home(), collected)
        elif is_running_as_root():
            # MDM / root — walk every user's sessions tree.
            def _extract_for_user(user_home: Path) -> None:
                self._collect_from(_sessions_dir_for_user(user_home), user_home, collected)
            scan_user_directories(_extract_for_user)
        else:
            # Normal user.
            self._collect_from(_sessions_dir_for_user(Path.home()), Path.home(), collected)

        return {"user_skills": deduplicate_skills(collected), "project_skills": []}

    def _collect_from(self, sessions_root: Path, user_home: Path, collected: List[Dict]) -> None:
        """Walk one user's sessions tree, appending skill dicts to *collected*."""
        try:
            if not sessions_root.exists() or not sessions_root.is_dir():
                return
        except OSError as e:
            logger.debug(f"Error accessing Cowork sessions dir {sessions_root}: {e}")
            return

        try:
            for candidate in sessions_root.rglob("*"):
                try:
                    if not candidate.is_file():
                        continue
                    if candidate.name.lower() != SKILL_FILE_NAME_LOWER:
                        continue
                    if is_ephemeral_session_path(candidate):
                        continue
                    if is_claude_code_path(candidate):
                        continue
                    skill_dict = build_cowork_skill_dict(candidate, user_home=user_home)
                    if skill_dict is not None:
                        collected.append(skill_dict)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {candidate}: {e}")
                    continue
                except Exception as e:
                    logger.debug(f"Error processing Cowork candidate {candidate}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error walking Cowork sessions dir {sessions_root}: {e}")
