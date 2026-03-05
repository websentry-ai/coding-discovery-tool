"""
Claude Code skills extraction for Windows systems.

Extracts Claude Code skills (SKILL.md files) from all projects on the user's machine,
grouping them by project root.

Skills are stored in:
- User-level: ~/.claude/skills/<skill-name>/SKILL.md
- Project-level: **/.claude/skills/<skill-name>/SKILL.md
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional

from ...coding_tool_base import BaseClaudeSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    should_skip_path,
    is_running_as_admin,
)

logger = logging.getLogger(__name__)

CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"


def _is_skill_md_file(filename: str) -> bool:
    """Check if filename is a SKILL.md file (case-insensitive)."""
    return filename.lower() == "skill.md"


def build_skills_project_list(projects_by_root: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Convert projects dictionary to list format with 'skills' key.

    Args:
        projects_by_root: Dictionary mapping project_root to list of skills

    Returns:
        List of project dicts with project_root and skills
    """
    return [
        {
            "project_root": project_root,
            "skills": skills
        }
        for project_root, skills in projects_by_root.items()
    ]


def find_skill_project_root(skill_file: Path) -> Path:
    """
    Find the project root directory for a Claude Code skill file.

    For skills:
    - User-level: ~/.claude/skills/<skill-name>/SKILL.md -> home directory
    - Project-level: <project>/.claude/skills/<skill-name>/SKILL.md -> project directory

    Args:
        skill_file: Path to the SKILL.md file

    Returns:
        Project root path
    """
    # SKILL.md is inside <skill-name> directory, which is inside skills/, which is inside .claude/
    # So: skill_file.parent = <skill-name>
    #     skill_file.parent.parent = skills/
    #     skill_file.parent.parent.parent = .claude/
    #     skill_file.parent.parent.parent.parent = project_root

    skill_dir = skill_file.parent  # <skill-name>
    skills_dir = skill_dir.parent  # skills/
    claude_dir = skills_dir.parent  # .claude/

    # Verify the directory structure
    if skills_dir.name == SKILLS_DIR_NAME and claude_dir.name == CLAUDE_DIR_NAME:
        return claude_dir.parent  # project root

    # Fallback: use the parent of .claude if we can find it
    for parent in skill_file.parents:
        if parent.name == CLAUDE_DIR_NAME:
            return parent.parent

    # Last resort: use the skill file's parent
    return skill_file.parent


def _extract_skill_info(skill_file: Path, scope: str = None) -> Optional[Dict]:
    """
    Extract skill information from a SKILL.md file.

    Returns a dict in the same format as rules, with additional skill-specific fields:
    - type: "skill" (to distinguish from rules)
    - skill_name: The skill directory name (e.g., "commit", "review-pr")

    Args:
        skill_file: Path to the SKILL.md file
        scope: Scope of the skill ("user" or "project")

    Returns:
        Dict with skill info in unified rules format, or None if extraction fails
    """
    rule_info = extract_single_rule_file(skill_file, find_skill_project_root, scope=scope)

    if rule_info:
        # Add skill-specific fields
        skill_name = skill_file.parent.name  # The skill directory name
        rule_info["skill_name"] = skill_name
        rule_info["type"] = "skill"
        # Keep file_name as-is

    return rule_info


class WindowsClaudeSkillsExtractor(BaseClaudeSkillsExtractor):
    """Extractor for Claude Code skills on Windows systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract all Claude Code skills from all projects on Windows.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills = []
        projects_by_root = {}

        # Extract user-level skills from ~/.claude/skills/
        self._extract_user_level_skills(user_skills)

        # Extract project-level skills from **/.claude/skills/
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)

        logger.info(f"Searching for Claude skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root)
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """
        Extract user-level skills from ~/.claude/skills/ directory.

        Args:
            user_skills: List to populate with user-level skills
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract user-level skills for a specific user."""
            skills_dir = user_home / CLAUDE_DIR_NAME / SKILLS_DIR_NAME

            if not skills_dir.exists() or not skills_dir.is_dir():
                return

            try:
                # Iterate over skill directories
                for skill_dir in skills_dir.iterdir():
                    if skill_dir.is_dir():
                        # Look for SKILL.md (case-insensitive) in skill directory
                        for item in skill_dir.iterdir():
                            if item.is_file() and _is_skill_md_file(item.name):
                                skill_info = _extract_skill_info(item, scope="user")
                                if skill_info:
                                    # Remove project_root from user skills (it's the home dir, not meaningful)
                                    skill_info.pop('project_root', None)
                                    user_skills.append(skill_info)
                                break  # Only one SKILL.md per skill directory
            except Exception as e:
                logger.debug(f"Error extracting user-level skills for {user_home}: {e}")

        # When running as admin, scan all user directories
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
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
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir()
                              if item.is_dir() and not should_skip_path(item, system_dirs)]

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
        Recursively walk directory tree looking for .claude/skills directories.

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
                    system_dirs = self._get_system_directories()
                    if should_skip_path(item, system_dirs):
                        continue

                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        # Check if this is a .claude directory
                        if item.name == CLAUDE_DIR_NAME:
                            # Check for skills subdirectory
                            skills_dir = item / SKILLS_DIR_NAME
                            if skills_dir.exists() and skills_dir.is_dir():
                                # Skip user-level skills (already extracted)
                                if self._is_user_level_skills_dir(skills_dir):
                                    continue
                                # Extract skills from this .claude/skills directory
                                self._extract_skills_from_directory(skills_dir, projects_by_root)
                            # Don't recurse into .claude directory
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

    def _is_user_level_skills_dir(self, skills_dir: Path) -> bool:
        """
        Check if a skills directory is at the user level (in home directory).

        Args:
            skills_dir: Path to the skills directory

        Returns:
            True if this is a user-level skills directory
        """
        try:
            # skills_dir is ~/.claude/skills or C:\Users\<user>\.claude\skills
            claude_dir = skills_dir.parent
            parent_of_claude = claude_dir.parent

            # Check if parent of .claude is a home directory
            if parent_of_claude == Path.home():
                return True

            # For admin scanning, check if it's under C:\Users\<username>
            if str(parent_of_claude).startswith('C:\\Users\\'):
                parent_parts = parent_of_claude.parts
                # C:\Users\<username> has 3 parts: ('C:\\', 'Users', '<username>')
                if len(parent_parts) == 3:
                    return True

            return False
        except Exception:
            return False

    def _extract_skills_from_directory(self, skills_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all skills from a .claude/skills directory.

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
                        if item.is_file() and _is_skill_md_file(item.name):
                            skill_info = _extract_skill_info(item, scope="project")
                            if skill_info:
                                project_root = skill_info.get('project_root')
                                if project_root:
                                    self._add_skill_to_project(skill_info, project_root, projects_by_root)
                            break  # Only one SKILL.md per skill directory
        except Exception as e:
            logger.debug(f"Error extracting skills from {skills_dir}: {e}")

    def _add_skill_to_project(
        self,
        skill_info: Dict,
        project_root: str,
        projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Add a skill to the appropriate project in the dictionary.

        Args:
            skill_info: Skill file information dict
            project_root: Project root path as string
            projects_by_root: Dictionary to update
        """
        if project_root not in projects_by_root:
            projects_by_root[project_root] = []

        # Remove project_root from skill since it's now at project level
        skill_without_root = {k: v for k, v in skill_info.items() if k != 'project_root'}
        projects_by_root[project_root].append(skill_without_root)

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.

        Returns:
            Set of system directory names
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
