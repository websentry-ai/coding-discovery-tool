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
    is_marketplace_catalog_path,
)
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories
from ...path_containment import is_within, owned_boundary

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
            # Explicit root (tests). The tempdir is not under a home, so the
            # ownership check is off here; every real walk below keeps it on.
            self._collect_from(self._explicit_sessions_root, Path.home(), collected, enforce_owner=False)
        elif is_running_as_root():
            # MDM / root — walk every user's sessions tree.
            # Deduplicate per-user to avoid cross-user skill name collisions.
            def _extract_for_user(user_home: Path) -> None:
                user_collected: List[Dict] = []
                self._collect_from(_sessions_dir_for_user(user_home), user_home, user_collected)
                collected.extend(deduplicate_skills(user_collected))
            scan_user_directories(_extract_for_user)
        else:
            # Normal user.
            self._collect_from(_sessions_dir_for_user(Path.home()), Path.home(), collected)

        return {"user_skills": collected if is_running_as_root() else deduplicate_skills(collected), "project_skills": []}

    def _collect_from(self, sessions_root: Path, user_home: Path, collected: List[Dict],
                      enforce_owner: bool = True) -> None:
        """Walk one user's sessions tree, appending skill dicts to *collected*.

        With ``enforce_owner`` a SKILL.md is read only if its real path is in the
        sessions tree and the tree's real path is in ``user_home``. Under a root
        scan a user could otherwise symlink their tree or a SKILL.md at another
        user's files, and root would read them and file them under this user.
        """
        try:
            if not sessions_root.exists() or not sessions_root.is_dir():
                return
        except OSError as e:
            logger.debug(f"Error accessing Cowork sessions dir {sessions_root}: {e}")
            return

        boundary = owned_boundary(user_home, sessions_root) if enforce_owner else None
        if enforce_owner and boundary is None:
            logger.debug(f"Skipping Cowork sessions dir {sessions_root}: resolves outside {user_home}")
            return

        try:
            for candidate in sessions_root.rglob("*"):
                try:
                    if not candidate.is_file():
                        continue
                    if candidate.name.lower() != SKILL_FILE_NAME_LOWER:
                        continue
                    if boundary is not None and not is_within(candidate, boundary):
                        continue
                    if is_ephemeral_session_path(candidate):
                        continue
                    if is_marketplace_catalog_path(candidate):
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
