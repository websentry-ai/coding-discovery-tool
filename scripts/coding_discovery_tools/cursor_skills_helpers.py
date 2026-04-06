"""
Shared helper functions for Cursor skills and commands extraction.

This module mirrors the config-driven design from claude_code_skills_helpers,
but handles Cursor's dual parent directories (.cursor and .agents).
Each item type (skill, command) is described by an ItemTypeConfig,
and generic functions handle finding project roots, extracting info, and
iterating directories for any item type.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable

from .claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    is_command_md_file,
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

CURSOR_DIR_NAME = ".cursor"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"
COMMANDS_DIR_NAME = "commands"

# Both directories that can serve as Cursor's tool root
CURSOR_PARENT_DIR_NAMES = (CURSOR_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

CURSOR_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

CURSOR_COMMAND_CONFIG = ItemTypeConfig(
    type_name="command",
    dir_name=COMMANDS_DIR_NAME,
    layout="flat",
    file_filter=is_command_md_file,
    name_extractor=lambda f: f.stem,
)

CURSOR_ITEM_CONFIGS = [CURSOR_SKILL_CONFIG, CURSOR_COMMAND_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Generic config-driven functions (Cursor-specific: checks .cursor AND .agents)
# ──────────────────────────────────────────────────────────────────────────────

def find_cursor_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """
    Find the project root directory for a Cursor item file.

    Same logic as find_item_project_root in claude_code_skills_helpers, but
    checks tool_dir.name in (CURSOR_DIR_NAME, AGENTS_DIR_NAME) instead of
    == CLAUDE_DIR_NAME.

    For nested layout (skills):
        item_file lives at <project>/.cursor/skills/<name>/SKILL.md
        Navigate: parent (name dir) -> parent (skills/) -> parent (.cursor/) -> verify -> parent (project root)

    For flat layout (commands):
        item_file lives at <project>/.cursor/commands/<name>.md
        Navigate: parent (commands/) -> parent (.cursor/) -> verify -> parent (project root)

    Falls back to walking up parents to find a .cursor or .agents directory,
    and as a last resort returns item_file.parent.

    Args:
        item_file: Path to the item file (e.g. SKILL.md or a command .md)
        config: ItemTypeConfig describing this item type

    Returns:
        Project root path
    """
    if config.layout == "nested":
        item_name_dir = item_file.parent       # <skill-name>
        type_dir = item_name_dir.parent        # skills/
        tool_dir = type_dir.parent             # .cursor/ or .agents/

        if type_dir.name == config.dir_name and tool_dir.name in CURSOR_PARENT_DIR_NAMES:
            return tool_dir.parent
    else:
        type_dir = item_file.parent            # commands/
        tool_dir = type_dir.parent             # .cursor/ or .agents/

        if type_dir.name == config.dir_name and tool_dir.name in CURSOR_PARENT_DIR_NAMES:
            return tool_dir.parent

    # Fallback: walk up parents to find .cursor or .agents
    for parent in item_file.parents:
        if parent.name in CURSOR_PARENT_DIR_NAMES:
            return parent.parent

    # Last resort
    return item_file.parent


def extract_cursor_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """
    Extract information from a Cursor item file using the given extraction function.

    Creates a closure that binds config to find_cursor_item_project_root, then
    delegates to extract_single_rule_file_func. On success, annotates the
    result with skill_name (derived via config.name_extractor) and type.

    Args:
        item_file: Path to the item file
        extract_single_rule_file_func: OS-specific function to extract rule file info.
            Expected signature: (file_path, find_root_func, scope=...) -> Optional[Dict]
        scope: Scope of the item ("user" or "project")
        config: ItemTypeConfig describing this item type

    Returns:
        Dict with item info in unified rules format, or None if extraction fails
    """
    find_root = lambda f: find_cursor_item_project_root(f, config)
    rule_info = extract_single_rule_file_func(item_file, find_root, scope=scope)

    if rule_info:
        rule_info["skill_name"] = config.name_extractor(item_file)
        rule_info["type"] = config.type_name

    return rule_info


def extract_cursor_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """
    Extract all items of a given type from a .cursor/<type> or .agents/<type> directory.

    For nested layout (skills): iterates subdirectories, finds the first
    matching file in each, extracts info, and adds to projects_by_root.

    For flat layout (commands): iterates files directly, extracts
    info for each matching file, and adds to projects_by_root.

    Args:
        type_dir: Path to the type directory (e.g. .cursor/skills/)
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
                            item_info = extract_cursor_item_info(
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
                    item_info = extract_cursor_item_info(
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


def extract_cursor_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """
    Extract user-level items (skills, commands) from a user's home directory.

    Iterates over both .cursor and .agents parent directories, and for each
    config locates the corresponding subdirectory and extracts items with
    scope="user". Each extracted item's project_root key is renamed to
    project_path before appending to user_skills.

    Args:
        user_home: Path to the user's home directory
        user_skills: List to populate with user-level item dicts
        extract_single_rule_file_func: OS-specific function to extract rule file info
        configs: List of ItemTypeConfig instances to process
    """
    for tool_dir_name in CURSOR_PARENT_DIR_NAMES:
        for config in configs:
            type_dir = user_home / tool_dir_name / config.dir_name
            if not type_dir.exists() or not type_dir.is_dir():
                continue

            try:
                if config.layout == "nested":
                    for subdir in type_dir.iterdir():
                        if subdir.is_dir():
                            for item in subdir.iterdir():
                                if item.is_file() and config.file_filter(item.name):
                                    item_info = extract_cursor_item_info(
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
                            item_info = extract_cursor_item_info(
                                item,
                                extract_single_rule_file_func,
                                scope="user",
                                config=config,
                            )
                            if item_info:
                                item_info["project_path"] = item_info.pop("project_root", None)
                                user_skills.append(item_info)
            except Exception as e:
                logger.debug(f"Error extracting user-level {config.type_name}s from {type_dir}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Backward-compatible aliases (old per-type functions delegate to generics)
# ──────────────────────────────────────────────────────────────────────────────

def find_cursor_skill_project_root(skill_file: Path) -> Path:
    """Find the project root for a Cursor skill file. Delegates to generic."""
    return find_cursor_item_project_root(skill_file, CURSOR_SKILL_CONFIG)


def find_cursor_command_project_root(command_file: Path) -> Path:
    """Find the project root for a Cursor command file. Delegates to generic."""
    return find_cursor_item_project_root(command_file, CURSOR_COMMAND_CONFIG)


def extract_cursor_skill_info(
    skill_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """Extract skill information from a SKILL.md file. Delegates to generic."""
    return extract_cursor_item_info(skill_file, extract_single_rule_file_func, scope, CURSOR_SKILL_CONFIG)


def extract_cursor_command_info(
    command_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
) -> Optional[Dict]:
    """Extract command information from a command .md file. Delegates to generic."""
    return extract_cursor_item_info(command_file, extract_single_rule_file_func, scope, CURSOR_COMMAND_CONFIG)


def extract_cursor_skills_from_directory(
    skills_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
) -> None:
    """Extract all skills from a .cursor/skills directory. Delegates to generic."""
    extract_cursor_items_from_directory(skills_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, CURSOR_SKILL_CONFIG)


def extract_cursor_commands_from_directory(
    commands_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
) -> None:
    """Extract all commands from a .cursor/commands directory. Delegates to generic."""
    extract_cursor_items_from_directory(commands_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, CURSOR_COMMAND_CONFIG)
