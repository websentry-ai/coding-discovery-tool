"""
Shared helper functions for Cursor skills extraction.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable

from .claude_code_skills_helpers import (
    is_skill_md_file,
    is_command_md_file,
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_skills_dir,
)

logger = logging.getLogger(__name__)

CURSOR_DIR_NAME = ".cursor"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"
COMMANDS_DIR_NAME = "commands"


def find_cursor_skill_project_root(skill_file: Path) -> Path:
    """
    Find the project root directory for a Cursor skill file.

    For skills:
    - User-level: ~/.cursor/skills/<skill-name>/SKILL.md -> home directory
    - Project-level: <project>/.cursor/skills/<skill-name>/SKILL.md -> project directory

    Args:
        skill_file: Path to the SKILL.md file

    Returns:
        Project root path
    """
    # SKILL.md is inside <skill-name> directory, which is inside skills/, which is inside .cursor/
    # So: skill_file.parent = <skill-name>
    #     skill_file.parent.parent = skills/
    #     skill_file.parent.parent.parent = .cursor/
    #     skill_file.parent.parent.parent.parent = project_root

    skill_dir = skill_file.parent  # <skill-name>
    skills_dir = skill_dir.parent  # skills/
    cursor_dir = skills_dir.parent  # .cursor/

    # Verify the directory structure
    if skills_dir.name == SKILLS_DIR_NAME and cursor_dir.name == CURSOR_DIR_NAME:
        return cursor_dir.parent  # project root

    # Fallback: use the parent of .cursor if we can find it
    for parent in skill_file.parents:
        if parent.name == CURSOR_DIR_NAME:
            return parent.parent

    # Last resort: use the skill file's parent
    return skill_file.parent


def find_cursor_command_project_root(command_file: Path) -> Path:
    """
    Find the project root directory for a Cursor command file.

    For commands:
    - User-level: ~/.cursor/commands/<name>.md -> home directory
    - Project-level: <project>/.cursor/commands/<name>.md -> project directory

    Args:
        command_file: Path to the command .md file

    Returns:
        Project root path
    """
    commands_dir = command_file.parent   # commands/
    cursor_dir = commands_dir.parent     # .cursor/

    if commands_dir.name == COMMANDS_DIR_NAME and cursor_dir.name == CURSOR_DIR_NAME:
        return cursor_dir.parent

    for parent in command_file.parents:
        if parent.name == CURSOR_DIR_NAME:
            return parent.parent

    return command_file.parent


def extract_cursor_command_info(
    command_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """
    Extract command information from a command .md file in a Cursor project.

    Returns a dict with additional command-specific fields:
    - type: "command" (to distinguish from skills)
    - skill_name: The command filename stem (e.g., "code-review" from code-review.md)

    Args:
        command_file: Path to the command .md file
        extract_single_rule_file_func: OS-specific function to extract rule file info
        scope: Scope of the command ("user" or "project")

    Returns:
        Dict with command info in unified rules format, or None if extraction fails
    """
    rule_info = extract_single_rule_file_func(command_file, find_cursor_command_project_root, scope=scope)

    if rule_info:
        rule_info["skill_name"] = command_file.stem
        rule_info["type"] = "command"

    return rule_info


def extract_cursor_commands_from_directory(
    commands_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable
) -> None:
    """
    Extract all commands from a .cursor/commands directory.

    Args:
        commands_dir: Path to the commands directory
        projects_by_root: Dictionary to populate with commands
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add command to project dict (handles thread safety)
    """
    try:
        for item in commands_dir.iterdir():
            if item.is_file() and is_command_md_file(item.name):
                command_info = extract_cursor_command_info(
                    item,
                    extract_single_rule_file_func,
                    scope="project"
                )
                if command_info:
                    project_root = command_info.get('project_root')
                    if project_root:
                        add_skill_func(command_info, project_root, projects_by_root)
    except Exception as e:
        logger.debug(f"Error extracting Cursor commands from {commands_dir}: {e}")


def extract_cursor_skill_info(
    skill_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """
    Extract skill information from a SKILL.md file in a Cursor project.

    Returns a dict in the same format as rules, with additional skill-specific fields:
    - type: "skill" (to distinguish from rules)
    - skill_name: The skill directory name

    Args:
        skill_file: Path to the SKILL.md file
        extract_single_rule_file_func: OS-specific function to extract rule file info
        scope: Scope of the skill ("user" or "project") - required

    Returns:
        Dict with skill info in unified rules format, or None if extraction fails
    """
    rule_info = extract_single_rule_file_func(skill_file, find_cursor_skill_project_root, scope=scope)

    if rule_info:
        # Add skill-specific fields
        skill_name = skill_file.parent.name  # The skill directory name
        rule_info["skill_name"] = skill_name
        rule_info["type"] = "skill"

    return rule_info


def extract_cursor_skills_from_directory(
    skills_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable
) -> None:
    """
    Extract all skills from a .cursor/skills directory.

    Args:
        skills_dir: Path to the skills directory
        projects_by_root: Dictionary to populate with skills
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add skill to project dict (handles thread safety)
    """
    try:
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                for item in skill_dir.iterdir():
                    if item.is_file() and is_skill_md_file(item.name):
                        skill_info = extract_cursor_skill_info(
                            item,
                            extract_single_rule_file_func,
                            scope="project"
                        )
                        if skill_info:
                            project_root = skill_info.get('project_root')
                            if project_root:
                                add_skill_func(skill_info, project_root, projects_by_root)
                        break  # Only one SKILL.md per skill directory
    except Exception as e:
        logger.debug(f"Error extracting Cursor skills from {skills_dir}: {e}")
