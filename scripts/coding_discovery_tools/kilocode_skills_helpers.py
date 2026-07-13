"""
Shared helper functions for Kilo Code skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.

Paths below were established with a LIVE ORACLE, not from the docs: the shipped
``bin/kilo`` binary (Kilo's agent runtime, an OpenCode fork) exposes
``kilo debug skill``, which lists every discovered skill and its absolute path.
Kilo's own docs claim only ``.kilo/`` + legacy ``.kilocode/``; the oracle showed
it additionally reads ``.claude``, ``.agents`` and ``~/.config/kilo`` (inherited
from its OpenCode lineage), and accepts a SINGULAR ``skill/`` dir as well as
``skills/``:

    User/global:  ~/.kilo/skills/<name>/SKILL.md
                  ~/.kilocode/skills/<name>/SKILL.md      (legacy)
                  ~/.config/kilo/skills/<name>/SKILL.md   (OpenCode-style config dir)
                  ~/.claude/skills/<name>/SKILL.md        (compat)
                  ~/.agents/skills/<name>/SKILL.md        (compat)
    Project:      <repo>/.kilo/{skills,skill}/<name>/SKILL.md
                  <repo>/.kilocode/{skills,skill}/<name>/SKILL.md  (legacy)
                  <repo>/.claude/skills/<name>/SKILL.md   (compat)
                  <repo>/.agents/skills/<name>/SKILL.md   (compat)

Like OpenCode/Windsurf, two parent-name sets are used because the user config dir
``~/.config/kilo`` is NESTED and its leaf (``kilo``) is not dotted:

- ``KILOCODE_PARENT_DIR_NAMES`` (dot-form) drives the project WALK + project root
  resolution. It deliberately omits the bare ``kilo`` so the walk never matches
  ``~/.config/kilo`` and double-counts a user skill as a project skill.
- ``KILOCODE_USER_PARENT_DIR_NAMES`` (with ``.config``) resolves user-level skills
  back to the owning home.

Kilo Code has no plugin system, so skills carry ``source="standalone"``.
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

KILO_DIR_NAME = ".kilo"
KILOCODE_LEGACY_DIR_NAME = ".kilocode"
CONFIG_KILO_USER_DIR = ".config/kilo"
AGENTS_DIR_NAME = ".agents"
CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_DIR_NAME_SINGULAR = "skill"
SKILL_FILE_NAME = "SKILL.md"

# Project-level parent dirs (dot-form). Drives the project walk + project root
# resolution. The bare ``kilo`` is intentionally excluded (see module docstring).
KILOCODE_PARENT_DIR_NAMES = (
    KILO_DIR_NAME,
    KILOCODE_LEGACY_DIR_NAME,
    CLAUDE_DIR_NAME,
    AGENTS_DIR_NAME,
)

# User-level dirs searched under each home. ``.config/kilo`` is a nested path
# (pathlib joins it correctly).
KILOCODE_USER_DIR_NAMES = (
    KILO_DIR_NAME,
    KILOCODE_LEGACY_DIR_NAME,
    CONFIG_KILO_USER_DIR,
    CLAUDE_DIR_NAME,
    AGENTS_DIR_NAME,
)

# Parent-name set used ONLY to resolve user-level skills back to the owning home.
# ``.config`` lets the generic fallback map ``~/.config/kilo/skills/x`` -> home.
KILOCODE_USER_PARENT_DIR_NAMES = (
    KILO_DIR_NAME,
    KILOCODE_LEGACY_DIR_NAME,
    ".config",
    CLAUDE_DIR_NAME,
    AGENTS_DIR_NAME,
)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────


def _kilocode_skill_config(dir_name: str) -> ItemTypeConfig:
    """Nested SKILL.md config for a given skills dir name (``skills`` or ``skill``)."""
    return ItemTypeConfig(
        type_name="skill",
        dir_name=dir_name,
        layout="nested",
        file_filter=is_skill_md_file,
        name_extractor=lambda f: f.parent.name,
    )


KILOCODE_SKILL_CONFIG = _kilocode_skill_config(SKILLS_DIR_NAME)
# Kilo accepts BOTH ``skills/`` and the singular ``skill/`` (docs-stated,
# oracle-confirmed: ``<proj>/.kilo/skill/<name>/SKILL.md`` is discovered).
KILOCODE_SKILL_CONFIG_SINGULAR = _kilocode_skill_config(SKILL_DIR_NAME_SINGULAR)

KILOCODE_ITEM_CONFIGS = [KILOCODE_SKILL_CONFIG, KILOCODE_SKILL_CONFIG_SINGULAR]

# ──────────────────────────────────────────────────────────────────────────────
# Kilo Code-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_kilocode_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Kilo Code item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=KILOCODE_PARENT_DIR_NAMES)



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
    """Extract user-level Kilo Code items from a user's home directory.

    Uses the nested ``.config/kilo`` + legacy ``.kilocode`` + compat dirs, and the
    ``.config``-aware parent set so ``~/.config/kilo/skills`` resolves to the home.
    """
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=KILOCODE_USER_DIR_NAMES,
        parent_dir_names=KILOCODE_USER_PARENT_DIR_NAMES,
    )
