"""
Cursor skills extraction for Windows systems.

Extracts Cursor skills from all projects,
grouping them by project root.

Skills:  ~/.cursor/skills/<name>/SKILL.md, **/.cursor/skills/<name>/SKILL.md
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    should_skip_path,
    is_running_as_admin,
)
from ...cursor_skills_helpers import (
    CURSOR_DIR_NAME,
    SKILLS_DIR_NAME,
    COMMANDS_DIR_NAME,
    is_skill_md_file,
    extract_cursor_skill_info,
    extract_cursor_command_info,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_skills_dir,
    is_command_md_file,
)

logger = logging.getLogger(__name__)

# Windows system directories to skip during scanning
WINDOWS_SYSTEM_DIRECTORIES = frozenset({
    'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
    'System Volume Information', '$Recycle.Bin', 'Recovery',
    'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
    'Config.Msi', 'Documents and Settings', 'MSOCache'
})


class WindowsCursorSkillsExtractor(BaseCursorSkillsExtractor):
    """Extractor for Cursor skills on Windows systems."""

    def __init__(self):
        """Initialize the extractor with thread synchronization."""
        super().__init__()
        self._lock = threading.Lock()

    def extract_all_skills(self) -> Dict:
        """
        Extract all Cursor skills from all projects on Windows.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills = []
        projects_by_root = {}

        # Extract user-level skills from ~/.cursor/skills/
        self._extract_user_level_skills(user_skills)

        # Extract project-level skills from **/.cursor/skills/
        # Use dynamic drive letter from home directory
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)

        logger.info(f"Searching for Cursor skills from root: {root_path}")
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
        # Home is typically C:\Users\<username>, so parent is C:\Users
        return Path.home().parent

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """
        Extract user-level skills from ~/.cursor/skills/ directory.

        Args:
            user_skills: List to populate with user-level skills
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract user-level skills for a specific user."""
            skills_dir = user_home / CURSOR_DIR_NAME / SKILLS_DIR_NAME
            if skills_dir.exists() and skills_dir.is_dir():
                try:
                    for skill_dir in skills_dir.iterdir():
                        if skill_dir.is_dir():
                            for item in skill_dir.iterdir():
                                if item.is_file() and is_skill_md_file(item.name):
                                    skill_info = extract_cursor_skill_info(
                                        item,
                                        extract_single_rule_file,
                                        scope="user"
                                    )
                                    if skill_info:
                                        skill_info["project_path"] = skill_info.pop("project_root", None)
                                        user_skills.append(skill_info)
                                    break
                except Exception as e:
                    logger.debug(f"Error extracting user-level Cursor skills for {user_home}: {e}")

            # Extract commands from ~/.cursor/commands/
            commands_dir = user_home / CURSOR_DIR_NAME / COMMANDS_DIR_NAME
            if commands_dir.exists() and commands_dir.is_dir():
                try:
                    for item in commands_dir.iterdir():
                        if item.is_file() and is_command_md_file(item.name):
                            command_info = extract_cursor_command_info(
                                item,
                                extract_single_rule_file,
                                scope="user"
                            )
                            if command_info:
                                command_info["project_path"] = command_info.pop("project_root", None)
                                user_skills.append(command_info)
                except Exception as e:
                    logger.debug(f"Error extracting user-level Cursor commands for {user_home}: {e}")

        # When running as admin, scan all user directories
        if is_running_as_admin():
            users_dir = self._get_users_directory()
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            extract_for_user(Path.home())

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level skills recursively from all projects.

        Args:
            root_path: Root directory to search from
            projects_by_root: Dictionary to populate with skills grouped by project root
        """
        try:
            top_level_dirs = [item for item in root_path.iterdir()
                              if item.is_dir() and not should_skip_path(item, WINDOWS_SYSTEM_DIRECTORIES)]

            # Use parallel processing for top-level directories
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
            # Fallback to sequential if parallel fails
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .cursor/skills directories.

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
                    # Performance fix: check skip condition without recreating set each iteration
                    if should_skip_path(item, WINDOWS_SYSTEM_DIRECTORIES):
                        continue

                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        # Check if this is a .cursor directory
                        if item.name == CURSOR_DIR_NAME:
                            users_root = str(self._get_users_directory())

                            skills_dir = item / SKILLS_DIR_NAME
                            if skills_dir.exists() and skills_dir.is_dir():
                                if not is_user_level_skills_dir(skills_dir, users_root):
                                    self._extract_skills_from_directory_threadsafe(skills_dir, projects_by_root)

                            # Also extract commands
                            commands_dir = item / COMMANDS_DIR_NAME
                            if commands_dir.exists() and commands_dir.is_dir():
                                if not is_user_level_skills_dir(commands_dir, users_root):
                                    self._extract_commands_from_directory_threadsafe(commands_dir, projects_by_root)

                            continue

                        # Recurse into other directories
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

    def _extract_skills_from_directory_threadsafe(
        self,
        skills_dir: Path,
        projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all skills from a .cursor/skills directory (thread-safe).

        Args:
            skills_dir: Path to the skills directory
            projects_by_root: Dictionary to populate with skills
        """
        try:
            # Iterate over skill directories inside skills/
            for skill_dir in skills_dir.iterdir():
                if skill_dir.is_dir():
                    # Look for SKILL.md (case-insensitive) in skill directory
                    for item in skill_dir.iterdir():
                        if item.is_file() and is_skill_md_file(item.name):
                            skill_info = extract_cursor_skill_info(
                                item,
                                extract_single_rule_file,
                                scope="project"
                            )
                            if skill_info:
                                project_root = skill_info.get('project_root')
                                if project_root:
                                    self._add_skill_to_project_threadsafe(
                                        skill_info,
                                        project_root,
                                        projects_by_root
                                    )
                            break  # Only one SKILL.md per skill directory
        except Exception as e:
            logger.debug(f"Error extracting Cursor skills from {skills_dir}: {e}")

    def _extract_commands_from_directory_threadsafe(
        self,
        commands_dir: Path,
        projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all commands from a .cursor/commands directory (thread-safe).

        Args:
            commands_dir: Path to the commands directory
            projects_by_root: Dictionary to populate with commands
        """
        try:
            for item in commands_dir.iterdir():
                if item.is_file() and is_command_md_file(item.name):
                    command_info = extract_cursor_command_info(
                        item,
                        extract_single_rule_file,
                        scope="project"
                    )
                    if command_info:
                        project_root = command_info.get('project_root')
                        if project_root:
                            self._add_skill_to_project_threadsafe(
                                command_info,
                                project_root,
                                projects_by_root
                            )
        except Exception as e:
            logger.debug(f"Error extracting Cursor commands from {commands_dir}: {e}")

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
