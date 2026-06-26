"""
Shared helper functions for Augment Code skills/commands extraction.

Delegates to the generic config-driven functions in ``claude_code_skills_helpers``,
passing Augment's directory names via the ``parent_dir_names`` / ``user_dir_names``
parameters. For Augment Code an agent skill is a subdirectory containing a
``SKILL.md`` and a command is a flat ``.md`` file:

  - User/global:  ~/.augment/skills/<name>/SKILL.md  and  ~/.augment/commands/*.md
  - Project:      <repo>/.augment/skills/<name>/SKILL.md
                  <repo>/.augment/commands/*.md

Augment has no plugin system, so every skill/command is ``source="standalone"``.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

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

AUGMENT_DIR_NAME = ".augment"
CLAUDE_DIR_NAME = ".claude"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
COMMANDS_DIR_NAME = "commands"
SKILL_FILE_NAME = "SKILL.md"

# Augment loads skills/commands from .augment, .claude AND .agents — in BOTH the
# workspace and the home dir (docs.augmentcode.com/cli/skills; .claude/commands is
# also honored for Claude compatibility). So the same .claude/.agents item is
# reported under Claude Code / Copilot CLI AND Augment; that is intentional — each
# tool reports what it actually loads, and the backend dedups per (tool, home_user).
AUGMENT_PARENT_DIR_NAMES = (AUGMENT_DIR_NAME, CLAUDE_DIR_NAME, AGENTS_DIR_NAME)
AUGMENT_USER_DIR_NAMES = (AUGMENT_DIR_NAME, CLAUDE_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

AUGMENT_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

AUGMENT_COMMAND_CONFIG = ItemTypeConfig(
    type_name="command",
    dir_name=COMMANDS_DIR_NAME,
    layout="flat",
    file_filter=is_command_md_file,
    name_extractor=lambda f: f.stem,
)

AUGMENT_ITEM_CONFIGS = [AUGMENT_SKILL_CONFIG, AUGMENT_COMMAND_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Augment-specific thin delegations to the generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_augment_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for an Augment skill/command file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=AUGMENT_PARENT_DIR_NAMES)


def extract_augment_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from an Augment skill/command file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=AUGMENT_PARENT_DIR_NAMES,
    )


def extract_augment_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all skills/commands from an Augment dir. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=AUGMENT_PARENT_DIR_NAMES,
    )


def extract_augment_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Augment skills/commands from a user's home directory.

    Looks under each of ``AUGMENT_USER_DIR_NAMES`` (``~/.augment``, ``~/.claude``,
    ``~/.agents``) since Augment loads home-scope skills/commands from all three.
    Delegates to the shared engine.
    """
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=AUGMENT_USER_DIR_NAMES,
        parent_dir_names=AUGMENT_PARENT_DIR_NAMES,
    )
