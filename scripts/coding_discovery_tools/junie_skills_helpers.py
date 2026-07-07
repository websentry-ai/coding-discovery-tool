"""
Shared helper functions for Junie skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Junie adopted the cross-vendor ``SKILL.md`` (Agent Skills) standard and
standardizes on the ``.junie`` directory at BOTH scopes:

    User/global:  ~/.junie/skills/<name>/SKILL.md
    Project:      <repo>/.junie/skills/<name>/SKILL.md

Junie has no plugin system, so skills carry ``source="standalone"``.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    find_item_project_root,
    extract_item_info,
    extract_items_from_directory,
    extract_user_level_items,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

JUNIE_DIR_NAME = ".junie"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Directories that can contain project-level Junie skills.
JUNIE_PARENT_DIR_NAMES = (JUNIE_DIR_NAME,)

# Directories searched under each user's home for user-level (global) skills.
JUNIE_USER_DIR_NAMES = (JUNIE_DIR_NAME,)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

JUNIE_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

JUNIE_ITEM_CONFIGS = [JUNIE_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Junie-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_junie_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Junie item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=JUNIE_PARENT_DIR_NAMES)


def extract_junie_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Junie item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=JUNIE_PARENT_DIR_NAMES,
    )


def extract_junie_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Junie directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=JUNIE_PARENT_DIR_NAMES,
    )


def extract_junie_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Junie items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=JUNIE_USER_DIR_NAMES,
        parent_dir_names=JUNIE_PARENT_DIR_NAMES,
    )
