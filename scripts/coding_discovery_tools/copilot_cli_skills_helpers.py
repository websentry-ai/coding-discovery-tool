"""
Shared helper functions for GitHub Copilot CLI skills extraction.

Delegates to the generic config-driven functions in ``claude_code_skills_helpers``,
passing Copilot CLI's directory names via the ``parent_dir_names`` /
``user_dir_names`` parameters. For the standalone ``@github/copilot`` CLI, an
agent skill is a subdirectory containing a ``SKILL.md`` (docs-verified locations):

  - User/global:  ~/.copilot/skills/<name>/SKILL.md  and  ~/.agents/skills/<name>/SKILL.md
  - Project:      <repo>/.github/skills/<name>/SKILL.md
                  <repo>/.claude/skills/<name>/SKILL.md
                  <repo>/.agents/skills/<name>/SKILL.md

Note: ``.claude`` and ``.agents`` are shared conventions — skills found there may
ALSO be reported by the Claude Code / Cursor extractors. That is intentional (a
skill under ``.claude/skills`` is genuinely executable by Copilot CLI); per-tool
deduplication happens downstream. Copilot CLI has no plugin system, so every
skill is ``source="standalone"``.
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

GITHUB_DIR_NAME = ".github"
CLAUDE_DIR_NAME = ".claude"
AGENTS_DIR_NAME = ".agents"
COPILOT_DIR_NAME = ".copilot"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Project-level skill roots Copilot CLI reads. .claude / .agents overlap with
# other tools (Claude Code / Cursor) — dedup is downstream, per-tool.
COPILOT_CLI_PARENT_DIR_NAMES = (GITHUB_DIR_NAME, CLAUDE_DIR_NAME, AGENTS_DIR_NAME)

# User/global skill roots: ~/.copilot/skills and ~/.agents/skills.
# (NOT .github — there is no ~/.github skills location.)
COPILOT_CLI_USER_DIR_NAMES = (COPILOT_DIR_NAME, AGENTS_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

COPILOT_CLI_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

COPILOT_CLI_ITEM_CONFIGS = [COPILOT_CLI_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Copilot CLI-specific thin delegations to the generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_copilot_cli_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Copilot CLI skill file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=COPILOT_CLI_PARENT_DIR_NAMES)


def extract_copilot_cli_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Copilot CLI skill file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=COPILOT_CLI_PARENT_DIR_NAMES,
    )


def extract_copilot_cli_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all skills from a Copilot CLI ``skills/`` directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=COPILOT_CLI_PARENT_DIR_NAMES,
    )


def extract_copilot_cli_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Copilot CLI skills from a user's home directory.

    The ``~/.copilot`` skills dir is resolved via ``_resolve_copilot_dir`` so a
    relocated ``COPILOT_HOME`` is honored — consistent with the detector / MCP /
    rules / settings extractors. ``~/.agents`` is a fixed home subdirectory not
    affected by ``COPILOT_HOME``. Both delegate to the shared engine.
    """
    # Lazy import to avoid an import cycle (macos/copilot_cli/__init__ -> the skills
    # extractor -> this helper). _resolve_copilot_dir is OS-agnostic; the Windows
    # extractors reuse it too.
    from .macos.copilot_cli.copilot_cli import _resolve_copilot_dir

    # ~/.copilot (or a relocated COPILOT_HOME): reuse the engine by expressing the
    # resolved dir as <parent>/<name> — for the default this is exactly
    # user_home/.copilot, so common-case behavior is unchanged.
    config_dir = _resolve_copilot_dir(user_home)
    extract_user_level_items(
        config_dir.parent, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=(config_dir.name,),
        parent_dir_names=COPILOT_CLI_PARENT_DIR_NAMES,
    )

    # ~/.agents (fixed home subdir; not relocated by COPILOT_HOME).
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=(AGENTS_DIR_NAME,),
        parent_dir_names=COPILOT_CLI_PARENT_DIR_NAMES,
    )
