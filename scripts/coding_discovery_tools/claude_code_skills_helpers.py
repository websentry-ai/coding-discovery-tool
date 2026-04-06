"""
Shared helper functions for Claude Code skills, commands, and agents extraction.

This module contains OS-agnostic functions used by both macOS and Windows
skills extractors to avoid code duplication. Uses a config-driven design
where each item type (skill, command, agent) is described by an ItemTypeConfig,
and generic functions handle finding project roots, extracting info, and
iterating directories for any item type.
"""

import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"
COMMANDS_DIR_NAME = "commands"
AGENTS_DIR_NAME = "agents"


# ──────────────────────────────────────────────────────────────────────────────
# File-filter helpers (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

class ItemTypeConfig(NamedTuple):
    """
    Describes one category of Claude Code item (skill, command, or agent).

    Attributes:
        type_name: Identifier used in output dicts, e.g. "skill", "command", "agent".
        dir_name: Subdirectory name under .claude/, e.g. "skills", "commands", "agents".
        layout: Either "nested" (items live in named subdirs containing a marker file)
                or "flat" (items are .md files directly inside the dir).
        file_filter: Predicate that returns True for filenames belonging to this type.
        name_extractor: Given the Path of the matched file, returns the item's name.
    """
    type_name: str
    dir_name: str
    layout: str
    file_filter: Callable
    name_extractor: Callable


SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

COMMAND_CONFIG = ItemTypeConfig(
    type_name="command",
    dir_name=COMMANDS_DIR_NAME,
    layout="flat",
    file_filter=is_command_md_file,
    name_extractor=lambda f: f.stem,
)

AGENT_CONFIG = ItemTypeConfig(
    type_name="agent",
    dir_name=AGENTS_DIR_NAME,
    layout="flat",
    file_filter=is_command_md_file,
    name_extractor=lambda f: f.stem,
)

CLAUDE_ITEM_CONFIGS = [SKILL_CONFIG, COMMAND_CONFIG, AGENT_CONFIG]


# ──────────────────────────────────────────────────────────────────────────────
# Project-list helpers (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

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

    # Remove project_root from skill since it's now at project level
    skill_without_root = {k: v for k, v in skill_info.items() if k != 'project_root'}
    projects_by_root[project_root].append(skill_without_root)


# ──────────────────────────────────────────────────────────────────────────────
# Generic config-driven functions
# ──────────────────────────────────────────────────────────────────────────────

def find_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """
    Find the project root directory for a Claude Code item file.

    For nested layout (skills):
        item_file lives at <project>/.claude/skills/<name>/SKILL.md
        Navigate: parent (name dir) -> parent (skills/) -> parent (.claude/) -> verify -> parent (project root)

    For flat layout (commands, agents):
        item_file lives at <project>/.claude/commands/<name>.md
        Navigate: parent (commands/) -> parent (.claude/) -> verify -> parent (project root)

    Falls back to walking up parents to find a .claude directory, and
    as a last resort returns item_file.parent.

    Args:
        item_file: Path to the item file (e.g. SKILL.md or a command .md)
        config: ItemTypeConfig describing this item type

    Returns:
        Project root path
    """
    if config.layout == "nested":
        # <skill-name>/SKILL.md  ->  skill_dir / skills_dir / claude_dir / project_root
        item_name_dir = item_file.parent       # <skill-name>
        type_dir = item_name_dir.parent        # skills/
        claude_dir = type_dir.parent           # .claude/

        if type_dir.name == config.dir_name and claude_dir.name == CLAUDE_DIR_NAME:
            return claude_dir.parent
    else:
        # flat: <name>.md  ->  type_dir / claude_dir / project_root
        type_dir = item_file.parent            # commands/ or agents/
        claude_dir = type_dir.parent           # .claude/

        if type_dir.name == config.dir_name and claude_dir.name == CLAUDE_DIR_NAME:
            return claude_dir.parent

    # Fallback: walk up parents to find .claude
    for parent in item_file.parents:
        if parent.name == CLAUDE_DIR_NAME:
            return parent.parent

    # Last resort
    return item_file.parent


def extract_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """
    Extract information from an item file using the given extraction function.

    Creates a closure that binds `config` to `find_item_project_root`, then
    delegates to `extract_single_rule_file_func`. On success, annotates the
    result with `skill_name` (derived via config.name_extractor) and `type`.

    Args:
        item_file: Path to the item file
        extract_single_rule_file_func: OS-specific function to extract rule file info.
            Expected signature: (file_path, find_root_func, scope=...) -> Optional[Dict]
        scope: Scope of the item ("user" or "project")
        config: ItemTypeConfig describing this item type

    Returns:
        Dict with item info in unified rules format, or None if extraction fails
    """
    find_root = lambda f: find_item_project_root(f, config)
    rule_info = extract_single_rule_file_func(item_file, find_root, scope=scope)

    if rule_info:
        rule_info["skill_name"] = config.name_extractor(item_file)
        rule_info["type"] = config.type_name

    return rule_info


def extract_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """
    Extract all items of a given type from a .claude/<type> directory.

    For nested layout (skills): iterates subdirectories, finds the first
    matching file in each, extracts info, and adds to projects_by_root.

    For flat layout (commands, agents): iterates files directly, extracts
    info for each matching file, and adds to projects_by_root.

    Args:
        type_dir: Path to the type directory (e.g. .claude/skills/)
        projects_by_root: Dictionary to populate with items
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add item to project dict (handles thread safety)
        config: ItemTypeConfig describing this item type
    """
    try:
        if config.layout == "nested":
            for subdir in type_dir.iterdir():
                if subdir.is_dir():
                    for item in subdir.iterdir():
                        if item.is_file() and config.file_filter(item.name):
                            item_info = extract_item_info(
                                item,
                                extract_single_rule_file_func,
                                scope="project",
                                config=config,
                            )
                            if item_info:
                                project_root = item_info.get("project_root")
                                if project_root:
                                    add_skill_func(item_info, project_root, projects_by_root)
                            break  # Only one marker file per subdirectory
        else:
            for item in type_dir.iterdir():
                if item.is_file() and config.file_filter(item.name):
                    item_info = extract_item_info(
                        item,
                        extract_single_rule_file_func,
                        scope="project",
                        config=config,
                    )
                    if item_info:
                        project_root = item_info.get("project_root")
                        if project_root:
                            add_skill_func(item_info, project_root, projects_by_root)
    except Exception as e:
        logger.debug(f"Error extracting {config.type_name}s from {type_dir}: {e}")


def extract_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """
    Extract user-level items (skills, commands, agents) from a user's home directory.

    Iterates over each config, locates the corresponding directory under
    ``user_home/.claude/<dir_name>``, and extracts items with scope="user".
    Each extracted item's ``project_root`` key is renamed to ``project_path``
    before appending to user_skills.

    Args:
        user_home: Path to the user's home directory
        user_skills: List to populate with user-level item dicts
        extract_single_rule_file_func: OS-specific function to extract rule file info
        configs: List of ItemTypeConfig instances to process
    """
    for config in configs:
        type_dir = user_home / CLAUDE_DIR_NAME / config.dir_name
        if not type_dir.exists() or not type_dir.is_dir():
            continue

        try:
            if config.layout == "nested":
                for subdir in type_dir.iterdir():
                    if subdir.is_dir():
                        for item in subdir.iterdir():
                            if item.is_file() and config.file_filter(item.name):
                                item_info = extract_item_info(
                                    item,
                                    extract_single_rule_file_func,
                                    scope="user",
                                    config=config,
                                )
                                if item_info:
                                    item_info["project_path"] = item_info.pop("project_root", None)
                                    user_skills.append(item_info)
                                break  # Only one marker file per subdirectory
            else:
                for item in type_dir.iterdir():
                    if item.is_file() and config.file_filter(item.name):
                        item_info = extract_item_info(
                            item,
                            extract_single_rule_file_func,
                            scope="user",
                            config=config,
                        )
                        if item_info:
                            item_info["project_path"] = item_info.pop("project_root", None)
                            user_skills.append(item_info)
        except Exception as e:
            logger.debug(f"Error extracting user-level {config.type_name}s for {user_home}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# User-level directory check (renamed from is_user_level_skills_dir)
# ──────────────────────────────────────────────────────────────────────────────

def is_user_level_claude_subdir(subdir: Path, users_root_path: str = None) -> bool:
    """
    Check if a .claude subdirectory is at the user level (in a home directory).

    Works for any .claude subdirectory — skills, commands, or agents.
    The directory must be of the form ``<home>/.claude/<subdir_name>`` where
    ``<home>`` is a direct child of the users root (e.g. /Users or C:\\Users).

    Args:
        subdir: Path to the .claude subdirectory (e.g. ~/.claude/skills)
        users_root_path: Optional path to users root (e.g., "/Users" or "C:\\Users").
                         If not provided, will be derived from the current home directory.

    Returns:
        True if this is a user-level .claude subdirectory
    """
    try:
        claude_dir = subdir.parent
        parent_of_claude = claude_dir.parent

        # Check if parent of .claude is the current user's home directory
        if parent_of_claude == Path.home():
            return True

        # Derive users root from home directory if not provided
        if users_root_path is None:
            home = Path.home()
            users_root_path = str(home.parent)
            if sys.platform == "darwin" and not users_root_path.startswith("/Users"):
                users_root_path = "/Users"
            elif sys.platform == "win32" and "Users" not in users_root_path:
                users_root_path = str(Path(home.anchor) / "Users")

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


# Backward-compatible alias — callers that still reference the old name will work.
is_user_level_skills_dir = is_user_level_claude_subdir
