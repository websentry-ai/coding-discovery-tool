"""
Shared helper functions for Kilo Code skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Kilo Code adopted the cross-vendor ``SKILL.md`` (Agent Skills) standard and
supports its own ``.kilo`` directory, the cross-vendor ``.agents`` directory,
and the ``.claude`` directory (compat) at project scope:

    User/global:  ~/.kilo/skills/<name>/SKILL.md
                  ~/.agents/skills/<name>/SKILL.md
    Project:      <repo>/.kilo/skills/<name>/SKILL.md
                  <repo>/.agents/skills/<name>/SKILL.md
                  <repo>/.claude/skills/<name>/SKILL.md  (compat)

Kilo Code has no plugin system, so skills carry ``source="standalone"``.
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

KILO_DIR_NAME = ".kilo"
AGENTS_DIR_NAME = ".agents"
CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Directories that can contain project-level Kilo Code skills.
KILOCODE_PARENT_DIR_NAMES = (KILO_DIR_NAME, AGENTS_DIR_NAME, CLAUDE_DIR_NAME)

# Directories searched under each user's home for user-level (global) skills.
KILOCODE_USER_DIR_NAMES = (KILO_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

KILOCODE_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

KILOCODE_ITEM_CONFIGS = [KILOCODE_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Kilo Code-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_kilocode_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Kilo Code item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=KILOCODE_PARENT_DIR_NAMES)


def extract_kilocode_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Kilo Code item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=KILOCODE_PARENT_DIR_NAMES,
    )


def extract_kilocode_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Kilo Code directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=KILOCODE_PARENT_DIR_NAMES,
    )


def extract_kilocode_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Kilo Code items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=KILOCODE_USER_DIR_NAMES,
        parent_dir_names=KILOCODE_PARENT_DIR_NAMES,
    )
