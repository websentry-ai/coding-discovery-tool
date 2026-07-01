"""
Shared helper functions for Cline skills extraction.

This module delegates to the generic functions in claude_code_skills_helpers,
passing Cline's three possible parent directories (.cline, .clinerules, .claude)
via the parent_dir_names parameter. Each item type is described by an
ItemTypeConfig, and the generic functions handle finding project roots,
extracting info, and iterating directories for any item type.

Cline only has skills (nested layout with SKILL.md), no commands.
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

CLINE_DIR_NAME = ".cline"
CLINERULES_DIR_NAME = ".clinerules"
CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# All directories that can contain project-level Cline skills.
# Note: .claude is intentionally included per Cline docs. Skills in .claude/skills/
# may also be reported by the Claude Code extractor — deduplication happens downstream.
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
# Cline-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_cline_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Cline item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=CLINE_PARENT_DIR_NAMES)


def extract_cline_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Cline item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=CLINE_PARENT_DIR_NAMES,
    )


def extract_cline_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Cline directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=CLINE_PARENT_DIR_NAMES,
    )


def extract_cline_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Cline items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=CLINE_USER_DIR_NAMES,
        parent_dir_names=CLINE_PARENT_DIR_NAMES,
    )
