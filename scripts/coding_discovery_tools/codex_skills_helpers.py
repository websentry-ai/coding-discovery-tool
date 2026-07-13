"""
Shared helper functions for OpenAI Codex skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Codex adopted the cross-vendor ``SKILL.md`` (Agent Skills) standard.

Paths verified against the CLI itself (codex 0.139.0), not just the docs page —
``codex app-server``'s ``skills/list`` reports each discovered skill with a scope
and an absolute path:

    User/global:  ~/.codex/skills/<name>/SKILL.md          (scope "user")
                  ~/.agents/skills/<name>/SKILL.md         (cross-agent alias)
    Project:      <repo>/.agents/skills/<name>/SKILL.md    (scope "repo")

``$CODEX_HOME/skills`` (default ``~/.codex/skills``) is where Codex's own bundled
``skill-creator`` / ``skill-installer`` skills WRITE new skills — "so Codex can
discover it automatically" — and the binary resolves it as
``os.path.join(_codex_home(), "skills")``. The docs page only mentions
``.agents/skills``, so relying on docs alone silently missed every user-installed
skill.

Because the user dir is ``.codex`` but the project dir is ``.agents``, two
parent-name sets are used (same technique as OpenCode/Windsurf):

- ``CODEX_PARENT_DIR_NAMES`` drives the project WALK + project root resolution.
  It deliberately EXCLUDES ``.codex`` so the walk never descends into
  ``~/.codex`` and over-collects vendor content — Codex keeps marketplace
  plugins at ``~/.codex/plugins/cache/...`` and OpenAI built-ins at
  ``~/.codex/skills/.system/`` (both reported by ``skills/list`` but neither is
  customer-authored config).
- ``CODEX_USER_PARENT_DIR_NAMES`` resolves user-level skills back to the home.
  User-scope extraction reads ``user_home/<dir>/skills`` DIRECTLY, so it is not
  affected by the walk's skip list.

Excluded on purpose: ``~/.codex/skills/.system/<name>/SKILL.md`` (OpenAI
built-ins) sit one level deeper than the ``skills/<name>/SKILL.md`` layout, so
the nested extractor naturally skips them. Plugin-bundled skills under
``~/.codex/plugins/cache/`` are a separate surface (Codex plugins), not skills
config the customer authored.

Known limitation: ``CODEX_HOME`` can relocate the user dir; only the ``~/.codex``
default is scanned (a root/all-users scan cannot know each user's environment).

Codex has no per-skill plugin provenance here, so skills carry
``source="standalone"``.
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
CODEX_DIR_NAME = ".codex"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Project-level parent dirs. Drives the project walk + project root resolution.
# ``.codex`` is deliberately absent so the walk never descends into ~/.codex
# (vendor plugins / .system built-ins) — see module docstring.
CODEX_PARENT_DIR_NAMES = (AGENTS_DIR_NAME,)

# Directories searched under each user's home for user-level (global) skills.
# ``.codex`` is where Codex's own skill-creator/skill-installer write skills.
CODEX_USER_DIR_NAMES = (CODEX_DIR_NAME, AGENTS_DIR_NAME)

# Parent-name set used ONLY to resolve user-level skills back to the owning home
# (``~/.codex/skills/x/SKILL.md`` -> home). User extraction reads the dirs
# directly, so this never re-enables the walk's ~/.codex skip.
CODEX_USER_PARENT_DIR_NAMES = (CODEX_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

CODEX_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

CODEX_ITEM_CONFIGS = [CODEX_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Codex-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_codex_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Codex item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=CODEX_PARENT_DIR_NAMES)



def extract_codex_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Codex directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=CODEX_PARENT_DIR_NAMES,
    )


def extract_codex_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Codex items from a user's home directory.

    Reads ``~/.codex/skills`` (where Codex itself installs skills) and the
    ``~/.agents/skills`` alias, resolving both back to the home via the
    ``.codex``-aware parent set.
    """
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=CODEX_USER_DIR_NAMES,
        parent_dir_names=CODEX_USER_PARENT_DIR_NAMES,
    )
