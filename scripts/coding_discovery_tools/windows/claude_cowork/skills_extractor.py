"""
Claude Cowork skills extraction for Windows.

Walks Claude Desktop's on-disk session tree at
``%APPDATA%\\Claude\\local-agent-mode-sessions\\`` looking for SKILL.md files.
Mirrors the macOS extractor — see that module for the design rationale.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseClaudeCoworkSkillsExtractor
from ...claude_cowork_skills_helpers import (
    SKILL_FILE_NAME_LOWER,
    build_cowork_skill_dict,
    deduplicate_skills,
    is_claude_code_path,
)
from .claude_cowork import _get_cowork_sessions_dir

logger = logging.getLogger(__name__)


class WindowsClaudeCoworkSkillsExtractor(BaseClaudeCoworkSkillsExtractor):
    """Extractor for Claude Cowork skills on Windows."""

    def __init__(self, sessions_root: Optional[Path] = None):
        # ``sessions_root`` is overridable so tests can point at a tempdir.
        self._sessions_root = sessions_root or _get_cowork_sessions_dir()

    def extract_all_skills(self) -> Dict:
        empty: Dict = {"user_skills": [], "project_skills": []}

        sessions_root = self._sessions_root
        if sessions_root is None:
            return empty

        try:
            if not sessions_root.exists() or not sessions_root.is_dir():
                return empty
        except OSError as e:
            logger.debug(f"Error accessing Cowork sessions dir {sessions_root}: {e}")
            return empty

        collected: List[Dict] = []
        try:
            for candidate in sessions_root.rglob("*"):
                try:
                    if not candidate.is_file():
                        continue
                    if candidate.name.lower() != SKILL_FILE_NAME_LOWER:
                        continue
                    # Ephemeral session skills are kept; build_cowork_skill_dict
                    # tags them with ``scope="session_ephemeral"``.
                    if is_claude_code_path(candidate):
                        continue
                    skill_dict = build_cowork_skill_dict(candidate)
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
            return empty

        deduped = deduplicate_skills(collected)
        return {"user_skills": deduped, "project_skills": []}
