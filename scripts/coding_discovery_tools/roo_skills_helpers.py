"""
Shared helper functions for Roo Code skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers,
with one Roo-specific twist: besides the standard ``skills/`` directory, Roo also
supports MODE-specific skill dirs named ``skills-{mode}/`` (e.g. ``skills-code``,
``skills-architect``) — mirroring Roo's existing ``rules-{mode}/`` convention.
Mode names are user-definable, so the set of skill dirs is discovered at runtime
rather than hard-coded into a fixed config list.

Paths (docs-verified):
    User/global:  ~/.roo/skills/<name>/SKILL.md, ~/.roo/skills-{mode}/<name>/SKILL.md
                  ~/.agents/skills/<name>/SKILL.md, ~/.agents/skills-{mode}/<name>/SKILL.md
    Project:      <repo>/.roo/skills/, <repo>/.roo/skills-{mode}/
                  <repo>/.agents/skills/, <repo>/.agents/skills-{mode}/

Roo has no plugin system, so skills carry ``source="standalone"``.
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List

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

ROO_DIR_NAME = ".roo"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
SKILLS_MODE_PREFIX = "skills-"
SKILL_FILE_NAME = "SKILL.md"

ROO_PARENT_DIR_NAMES = (ROO_DIR_NAME, AGENTS_DIR_NAME)
ROO_USER_DIR_NAMES = (ROO_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────


def _roo_skill_config(dir_name: str) -> ItemTypeConfig:
    """Build a nested SKILL.md config for a given skills dir name (``skills`` or
    ``skills-{mode}``). ``dir_name`` must match the on-disk directory so the
    generic project-root resolver recognises it."""
    return ItemTypeConfig(
        type_name="skill",
        dir_name=dir_name,
        layout="nested",
        file_filter=is_skill_md_file,
        name_extractor=lambda f: f.parent.name,
    )


# Canonical base config (the plain ``skills/`` dir) — exported for tests/parity.
ROO_SKILL_CONFIG = _roo_skill_config(SKILLS_DIR_NAME)
ROO_ITEM_CONFIGS = [ROO_SKILL_CONFIG]


def is_roo_skill_type_dirname(name: str) -> bool:
    """True for ``skills`` and any ``skills-{mode}`` directory name."""
    return name == SKILLS_DIR_NAME or name.startswith(SKILLS_MODE_PREFIX)


def iter_roo_skill_type_dirs(tool_dir: Path) -> List[Path]:
    """Return every ``skills`` / ``skills-{mode}`` directory directly under a Roo
    tool dir (``.roo`` or ``.agents``). Returns [] on any OS error."""
    found: List[Path] = []
    try:
        for child in tool_dir.iterdir():
            if child.is_dir() and is_roo_skill_type_dirname(child.name):
                found.append(child)
    except (PermissionError, OSError) as e:
        logger.debug(f"Error listing Roo skill dirs under {tool_dir}: {e}")
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Roo-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_roo_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Roo item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=ROO_PARENT_DIR_NAMES)


def extract_roo_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
) -> None:
    """Extract skills from one ``skills`` / ``skills-{mode}`` directory.

    Builds a config keyed to the directory's actual name so the generic root
    resolver recognises ``skills-{mode}`` dirs. Delegates to the generic engine.
    """
    config = _roo_skill_config(type_dir.name)
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=ROO_PARENT_DIR_NAMES,
    )


def extract_roo_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
) -> None:
    """Extract user-level Roo skills (``skills`` + all ``skills-{mode}``) from a
    user's home directory. Discovers the mode dirs present under any Roo user dir,
    then delegates to the generic user-level extractor with the discovered configs.
    """
    dir_names = set()
    for user_dir in ROO_USER_DIR_NAMES:
        base = user_home / user_dir
        if base.is_dir():
            for type_dir in iter_roo_skill_type_dirs(base):
                dir_names.add(type_dir.name)

    if not dir_names:
        return

    configs = [_roo_skill_config(name) for name in sorted(dir_names)]
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=ROO_USER_DIR_NAMES,
        parent_dir_names=ROO_PARENT_DIR_NAMES,
    )
