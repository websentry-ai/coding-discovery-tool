"""
Shared helper functions for OpenCode skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
OpenCode adopted the ``SKILL.md`` (Agent Skills) standard but its user/global
directory differs from its project directory (docs-verified):

    User/global:  ~/.config/opencode/skills/<name>/SKILL.md
                  ~/.claude/skills/<name>/SKILL.md   (compat)
                  ~/.agents/skills/<name>/SKILL.md   (compat)
    Project:      <repo>/.opencode/skills/<name>/SKILL.md
                  <repo>/.claude/skills/<name>/SKILL.md   (compat)
                  <repo>/.agents/skills/<name>/SKILL.md   (compat)

Because the project tool dir is ``.opencode`` while the user tool dir is the
NON-dotted ``opencode`` nested under ``.config``, two distinct parent-name sets
are used:

- ``OPENCODE_PARENT_DIR_NAMES`` (dot-form) drives the project WALK and project
  root resolution. It deliberately omits the bare ``opencode`` so the project
  walk never matches ``~/.config/opencode`` and double-counts a user skill as a
  project skill.
- ``OPENCODE_USER_PARENT_DIR_NAMES`` (with ``.config``) resolves user-level
  skills back to the owning home: for ``~/.config/opencode/skills/x/SKILL.md``
  the generic fallback walks up to ``.config`` and returns its parent (the home).

OpenCode has no plugin system, so skills carry ``source="standalone"``.
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

OPENCODE_DIR_NAME = ".opencode"
CLAUDE_DIR_NAME = ".claude"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Project-level parent dirs (dot-form). Drives the project walk + project root
# resolution. Bare ``opencode`` is intentionally excluded (see module docstring).
OPENCODE_PARENT_DIR_NAMES = (OPENCODE_DIR_NAME, CLAUDE_DIR_NAME, AGENTS_DIR_NAME)

# User-level dirs searched under each home. ``.config/opencode`` is a nested path
# (pathlib joins it correctly). ``.claude`` / ``.agents`` are compat aliases.
OPENCODE_USER_DIR_NAMES = (".config/opencode", CLAUDE_DIR_NAME, AGENTS_DIR_NAME)

# Parent-name set used ONLY to resolve user-level skills back to the owning home.
# ``.config`` lets the generic fallback map ``~/.config/opencode/skills/x`` -> home.
OPENCODE_USER_PARENT_DIR_NAMES = (".config", CLAUDE_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

OPENCODE_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

OPENCODE_ITEM_CONFIGS = [OPENCODE_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# OpenCode-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_opencode_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for an OpenCode project item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=OPENCODE_PARENT_DIR_NAMES)


def extract_opencode_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from an OpenCode project item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=OPENCODE_PARENT_DIR_NAMES,
    )


def extract_opencode_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from an OpenCode directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=OPENCODE_PARENT_DIR_NAMES,
    )


def extract_opencode_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level OpenCode items from a user's home directory.

    Uses the ``.config/opencode`` + compat user dirs, and the ``.config``-aware
    parent set so ``~/.config/opencode/skills`` resolves back to the home.
    """
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=OPENCODE_USER_DIR_NAMES,
        parent_dir_names=OPENCODE_USER_PARENT_DIR_NAMES,
    )
