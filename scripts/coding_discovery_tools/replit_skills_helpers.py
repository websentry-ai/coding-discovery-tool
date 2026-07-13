"""
Shared helper functions for Replit skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Replit adopted the cross-vendor ``SKILL.md`` (Agent Skills) standard and
standardizes on the cross-vendor ``.agents`` directory at PROJECT scope only:

    Project:      <repo>/.agents/skills/<name>/SKILL.md

Replit has NO local user/global skills path — global skills live server-side —
so ``REPLIT_USER_DIR_NAMES`` is intentionally an empty tuple.
Replit has no plugin system, so skills carry ``source="standalone"``.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    find_item_project_root,
    extract_items_from_directory,
    extract_user_level_items,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Directories that can contain project-level Replit skills.
REPLIT_PARENT_DIR_NAMES = (AGENTS_DIR_NAME,)

# Replit has NO local user/global skills path — global skills live server-side.
REPLIT_USER_DIR_NAMES = ()

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

REPLIT_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

REPLIT_ITEM_CONFIGS = [REPLIT_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Replit-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_replit_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Replit item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=REPLIT_PARENT_DIR_NAMES)



def extract_replit_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Replit directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=REPLIT_PARENT_DIR_NAMES,
    )


def extract_replit_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Replit items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=REPLIT_USER_DIR_NAMES,
        parent_dir_names=REPLIT_PARENT_DIR_NAMES,
    )
