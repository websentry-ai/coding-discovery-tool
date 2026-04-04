"""
Claude Code skills, commands, and agents extraction for Windows systems.

Extracts Claude Code skills, commands, and agents from all projects,
grouping them by project root.

Skills:   ~/.claude/skills/<name>/SKILL.md, **/.claude/skills/<name>/SKILL.md
Commands: ~/.claude/commands/<name>.md,     **/.claude/commands/<name>.md
Agents:   ~/.claude/agents/<name>.md,       **/.claude/agents/<name>.md
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClaudeSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)
from ...claude_code_skills_helpers import (
    CLAUDE_DIR_NAME,
    CLAUDE_ITEM_CONFIGS,
    build_skills_project_list,
    extract_items_from_directory,
    extract_user_level_items,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class WindowsClaudeSkillsExtractor(BaseClaudeSkillsExtractor):
    """Extractor for Claude Code skills on Windows systems."""

    def __init__(self) -> None:
        """Initialize the extractor with thread synchronization."""
        super().__init__()
        self._lock = threading.Lock()

    def extract_all_skills(self) -> Dict:
        """
        Extract all Claude Code skills from all projects on Windows.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_skills(user_skills)

        root_drive = Path.home().anchor  # e.g. "C:\"
        root_path = Path(root_drive)

        logger.info(f"Searching for Claude skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root)
        }

    def _get_users_directory(self) -> Path:
        """
        Get the Users directory dynamically based on home directory.

        Returns:
            Path to the Users directory (e.g., C:\\Users or D:\\Users)
        """
        return Path.home().parent

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """
        Extract user-level skills, commands, and agents from ~/.claude/.

        Args:
            user_skills: List to populate with user-level items
        """
        def extract_for_user(user_home: Path) -> None:
            extract_user_level_items(user_home, user_skills, extract_single_rule_file, CLAUDE_ITEM_CONFIGS)

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level skills recursively from all projects.

        Args:
            root_path: Root directory to search from
            projects_by_root: Dictionary to populate with skills grouped by project root
        """
        try:
            top_level_dirs = [item for item in root_path.iterdir()
                              if item.is_dir() and not should_skip_path(item, get_windows_system_directories())]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_skills, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .claude directories.

        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects_by_root: Dictionary to populate with skills
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item, get_windows_system_directories()):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name == CLAUDE_DIR_NAME:
                            users_root = str(self._get_users_directory())
                            for config in CLAUDE_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir():
                                    if not is_user_level_claude_subdir(type_dir, users_root):
                                        extract_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            self._add_skill_to_project_threadsafe,
                                            config,
                                        )
                            continue

                        self._walk_for_skills(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _add_skill_to_project_threadsafe(
        self,
        skill_info: Dict,
        project_root: str,
        projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Add a skill to the appropriate project in the dictionary (thread-safe).

        Uses a lock to prevent race conditions when multiple threads
        try to add skills to the same project.

        Args:
            skill_info: Skill file information dict
            project_root: Project root path as string
            projects_by_root: Dictionary to update
        """
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
