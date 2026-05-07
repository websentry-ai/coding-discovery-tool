"""
Shared helper functions for Cline skills extraction.

This module mirrors the config-driven design from cursor_skills_helpers,
but handles Cline's three possible project-level parent directories
(.cline, .clinerules, .claude). Each item type is described by an
ItemTypeConfig, and generic functions handle finding project roots,
extracting info, and iterating directories for any item type.

Cline only has skills (nested layout with SKILL.md), no commands.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

CLINE_DIR_NAME = ".cline"
CLINERULES_DIR_NAME = ".clinerules"
CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# All directories that can contain project-level Cline skills
CLINE_PARENT_DIR_NAMES = (CLINE_DIR_NAME, CLINERULES_DIR_NAME, CLAUDE_DIR_NAME)

# Only .cline for user-level (global) skills
CLINE_USER_DIR_NAMES = (CLINE_DIR_NAME,)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

CLINE_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

CLINE_ITEM_CONFIGS = [CLINE_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Generic config-driven functions (Cline-specific: checks 3 parent dirs)
# ──────────────────────────────────────────────────────────────────────────────


def find_cline_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """
    Find the project root directory for a Cline item file.

    For nested layout (skills):
        item_file lives at <project>/.cline/skills/<name>/SKILL.md
        Navigate: parent (name dir) -> parent (skills/) -> parent (.cline/) -> project root

    Also handles .clinerules/skills/ and .claude/skills/ parent directories.

    Falls back to walking up parents to find a matching parent directory,
    and as a last resort returns item_file.parent.

    Args:
        item_file: Path to the item file (e.g. SKILL.md)
        config: ItemTypeConfig describing this item type

    Returns:
        Project root path
    """
    if config.layout == "nested":
        item_name_dir = item_file.parent       # <skill-name>
        type_dir = item_name_dir.parent        # skills/
        tool_dir = type_dir.parent             # .cline/ or .clinerules/ or .claude/

        if type_dir.name == config.dir_name and tool_dir.name in CLINE_PARENT_DIR_NAMES:
            return tool_dir.parent
    else:
        type_dir = item_file.parent
        tool_dir = type_dir.parent

        if type_dir.name == config.dir_name and tool_dir.name in CLINE_PARENT_DIR_NAMES:
            return tool_dir.parent

    # Fallback: walk up parents to find a matching directory
    for parent in item_file.parents:
        if parent.name in CLINE_PARENT_DIR_NAMES:
            return parent.parent

    # Last resort
    return item_file.parent


def extract_cline_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """
    Extract information from a Cline item file using the given extraction function.

    Creates a closure that binds config to find_cline_item_project_root, then
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
    find_root = lambda f: find_cline_item_project_root(f, config)
    rule_info = extract_single_rule_file_func(item_file, find_root, scope=scope)

    if rule_info:
        rule_info["skill_name"] = config.name_extractor(item_file)
        rule_info["type"] = config.type_name

    return rule_info


def extract_cline_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """
    Extract all items of a given type from a Cline skills directory.

    For nested layout (skills): iterates subdirectories, finds the first
    matching file in each, extracts info, and adds to projects_by_root.

    Args:
        type_dir: Path to the type directory (e.g. .cline/skills/)
        projects_by_root: Dictionary to populate with items
        extract_single_rule_file_func: OS-specific function to extract rule file info
        add_skill_func: Function to add item to project dict
        config: ItemTypeConfig describing this item type
    """
    try:
        if config.layout == "nested":
            for subdir in type_dir.iterdir():
                if subdir.is_dir():
                    for item in subdir.iterdir():
                        if item.is_file() and config.file_filter(item.name):
                            item_info = extract_cline_item_info(
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
                    item_info = extract_cline_item_info(
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


def extract_cline_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """
    Extract user-level Cline items (skills) from a user's home directory.

    Only checks ~/.cline/skills/ for user-level skills (not .clinerules or .claude,
    which are project-only locations).

    Args:
        user_home: Path to the user's home directory
        user_skills: List to populate with user-level item dicts
        extract_single_rule_file_func: OS-specific function to extract rule file info
        configs: List of ItemTypeConfig instances to process
    """
    for tool_dir_name in CLINE_USER_DIR_NAMES:
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
                                    item_info = extract_cline_item_info(
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
                            item_info = extract_cline_item_info(
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
