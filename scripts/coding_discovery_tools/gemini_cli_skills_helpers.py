"""
Shared helper functions for Gemini CLI skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Gemini CLI adopted the cross-vendor ``SKILL.md`` (Agent Skills) standard and
supports both its own ``.gemini`` directory and the cross-vendor ``.agents``
directory at BOTH scopes:

    User/global:  ~/.gemini/skills/<name>/SKILL.md
                  ~/.agents/skills/<name>/SKILL.md
    Project:      <repo>/.gemini/skills/<name>/SKILL.md
                  <repo>/.agents/skills/<name>/SKILL.md

Gemini CLI has no plugin system, so skills carry ``source="standalone"``.
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

GEMINI_DIR_NAME = ".gemini"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Directories that can contain project-level Gemini CLI skills.
GEMINI_CLI_PARENT_DIR_NAMES = (GEMINI_DIR_NAME, AGENTS_DIR_NAME)

# Directories searched under each user's home for user-level (global) skills.
GEMINI_CLI_USER_DIR_NAMES = (GEMINI_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

GEMINI_CLI_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

GEMINI_CLI_ITEM_CONFIGS = [GEMINI_CLI_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Gemini CLI-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_gemini_cli_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Gemini CLI item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=GEMINI_CLI_PARENT_DIR_NAMES)


def extract_gemini_cli_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Gemini CLI item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=GEMINI_CLI_PARENT_DIR_NAMES,
    )


def extract_gemini_cli_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Gemini CLI directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=GEMINI_CLI_PARENT_DIR_NAMES,
    )


def extract_gemini_cli_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Gemini CLI items from a user's home directory. Delegates to generic."""
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=GEMINI_CLI_USER_DIR_NAMES,
        parent_dir_names=GEMINI_CLI_PARENT_DIR_NAMES,
    )
