"""
Shared helper functions for Claude Code skills extraction.

This module contains OS-agnostic functions used by both macOS and Windows
skills extractors to avoid code duplication.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable

logger = logging.getLogger(__name__)

CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"
COMMANDS_DIR_NAME = "commands"


def is_skill_md_file(filename: str) -> bool:
    """
    Check if filename is a SKILL.md file (case-insensitive).

    Args:
        filename: The filename to check

    Returns:
        True if the filename matches SKILL.md (case-insensitive)
    """
    return filename.lower() == SKILL_FILE_NAME.lower()


def is_command_md_file(filename: str) -> bool:
    """Check if filename is a markdown command file (excludes hidden files)."""
    return filename.lower().endswith(".md") and not filename.startswith(".")


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


def find_command_project_root(command_file: Path) -> Path:
    """
    Find the project root directory for a Claude Code command file.

    For commands:
    - User-level: ~/.claude/commands/<name>.md -> home directory
    - Project-level: <project>/.claude/commands/<name>.md -> project directory

    Args:
        command_file: Path to the command .md file

    Returns:
        Project root path
    """
    commands_dir = command_file.parent   # commands/
    claude_dir = commands_dir.parent     # .claude/

    if commands_dir.name == COMMANDS_DIR_NAME and claude_dir.name == CLAUDE_DIR_NAME:
        return claude_dir.parent

    for parent in command_file.parents:
        if parent.name == CLAUDE_DIR_NAME:
            return parent.parent

    return command_file.parent


def extract_command_info(
    command_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """
    Extract command information from a command .md file.
    """
    rule_info = extract_single_rule_file_func(command_file, find_command_project_root, scope=scope)

    if rule_info:
        rule_info["skill_name"] = command_file.stem
        rule_info["type"] = "command"

    return rule_info


def extract_commands_from_directory(
    commands_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable
) -> None:
    """
    Extract all commands from a .claude/commands directory.

    Args:
        commands_dir: Path to the commands directory
        projects_by_root: Dictionary to populate with commands
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add command to project dict (handles thread safety)
    """
    try:
        for item in commands_dir.iterdir():
            if item.is_file() and is_command_md_file(item.name):
                command_info = extract_command_info(
                    item,
                    extract_single_rule_file_func,
                    scope="project"
                )
                if command_info:
                    project_root = command_info.get('project_root')
                    if project_root:
                        add_skill_func(command_info, project_root, projects_by_root)
    except Exception as e:
        logger.debug(f"Error extracting commands from {commands_dir}: {e}")


def extract_skill_info(
    skill_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """
    Extract skill information from a SKILL.md file.

    Returns a dict in the same format as rules, with additional skill-specific fields:
    - type: "skill" (to distinguish from rules)
    - skill_name: The skill directory name (e.g., "commit", "review-pr")

    Args:
        skill_file: Path to the SKILL.md file
        extract_single_rule_file_func: OS-specific function to extract rule file info
        scope: Scope of the skill ("user" or "project") - required

    Returns:
        Dict with skill info in unified rules format, or None if extraction fails
    """
    rule_info = extract_single_rule_file_func(skill_file, find_skill_project_root, scope=scope)

    if rule_info:
        # Add skill-specific fields
        skill_name = skill_file.parent.name  # The skill directory name
        rule_info["skill_name"] = skill_name
        rule_info["type"] = "skill"

    return rule_info


def extract_skills_from_directory(
    skills_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable
) -> None:
    """
    Extract all skills from a .claude/skills directory.

    Args:
        skills_dir: Path to the skills directory
        projects_by_root: Dictionary to populate with skills
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add skill to project dict (handles thread safety)
    """
    try:
        # Iterate over skill directories inside skills/
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                # Look for SKILL.md (case-insensitive) in skill directory
                for item in skill_dir.iterdir():
                    if item.is_file() and is_skill_md_file(item.name):
                        skill_info = extract_skill_info(
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
        logger.debug(f"Error extracting skills from {skills_dir}: {e}")


def add_skill_to_project(
    skill_info: Dict,
    project_root: str,
    projects_by_root: Dict[str, List[Dict]]
) -> None:
    """
    Add a skill to the appropriate project in the dictionary.

    NOTE: This function is NOT thread-safe. For concurrent access,
    use add_skill_to_project_threadsafe() instead.

    Args:
        skill_info: Skill file information dict
        project_root: Project root path as string
        projects_by_root: Dictionary to update
    """
    if project_root not in projects_by_root:
        projects_by_root[project_root] = []

    # Rename project_root to project_path on the output object
    skill_for_project = {('project_path' if k == 'project_root' else k): v for k, v in skill_info.items()}
    projects_by_root[project_root].append(skill_for_project)


def is_user_level_skills_dir(skills_dir: Path, users_root_path: str = None) -> bool:
    """
    Check if a skills directory is at the user level (in home directory).

    Args:
        skills_dir: Path to the skills directory
        users_root_path: Optional path to users root (e.g., "/Users" or "C:\\Users")
                        If not provided, will be derived from home directory

    Returns:
        True if this is a user-level skills directory
    """
    try:
        # skills_dir is ~/.claude/skills or /Users/<user>/.claude/skills
        claude_dir = skills_dir.parent
        parent_of_claude = claude_dir.parent

        # Check if parent of .claude is the current user's home directory
        if parent_of_claude == Path.home():
            return True

        # Derive users root from home directory if not provided
        if users_root_path is None:
            # Get the users root dynamically (e.g., /Users on macOS, C:\Users on Windows)
            home = Path.home()
            # Home is typically /Users/<username> or C:\Users\<username>
            # So users root is home.parent
            users_root_path = str(home.parent)

        # Convert users_root_path to Path for consistent comparison across platforms
        # This handles both forward and backslash path separators
        users_root = Path(users_root_path)
        users_root_parts = users_root.parts
        parent_parts = parent_of_claude.parts

        # Check if parent_of_claude starts with users_root by comparing path parts
        # This is more reliable than string comparison across platforms
        if len(parent_parts) >= len(users_root_parts):
            if parent_parts[:len(users_root_parts)] == users_root_parts:
                # Check if this is directly under users root (e.g., /Users/john, not /Users/john/projects)
                # User home should have exactly one more part than users root
                # e.g., /Users has 2 parts, /Users/john has 3 parts
                if len(parent_parts) == len(users_root_parts) + 1:
                    return True

        return False
    except Exception:
        return False
