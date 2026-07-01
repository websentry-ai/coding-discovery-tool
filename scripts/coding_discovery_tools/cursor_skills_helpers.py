"""
Shared helper functions for Cursor skills and commands extraction.

This module delegates to the generic functions in claude_code_skills_helpers,
passing Cursor's dual parent directories (.cursor and .agents) via the
parent_dir_names parameter. Each item type (skill, command) is described by
an ItemTypeConfig, and the generic functions handle finding project roots,
extracting info, and iterating directories for any item type.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable

from .claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    is_command_md_file,
    find_item_project_root,
    extract_item_info,
    extract_items_from_directory,
    extract_user_level_items,
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
# Cursor-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_cursor_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Cursor item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=CURSOR_PARENT_DIR_NAMES)


def extract_cursor_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
    plugin_lookup: Optional[Dict] = None,
) -> Optional[Dict]:
    """Extract information from a Cursor item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=CURSOR_PARENT_DIR_NAMES,
        plugin_lookup=plugin_lookup,
    )


def extract_cursor_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
    plugin_lookup: Optional[Dict] = None,
) -> None:
    """Extract all items of a given type from a Cursor directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=CURSOR_PARENT_DIR_NAMES,
        plugin_lookup=plugin_lookup,
    )


def extract_cursor_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
    plugin_lookup: Optional[Dict] = None,
) -> None:
    """Extract user-level Cursor items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=CURSOR_PARENT_DIR_NAMES,
        parent_dir_names=CURSOR_PARENT_DIR_NAMES,
        plugin_lookup=plugin_lookup,
    )


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
