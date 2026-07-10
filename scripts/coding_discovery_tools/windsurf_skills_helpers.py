"""
Shared helper functions for Windsurf (Cascade) skills extraction.

Delegates to the generic config-driven functions in claude_code_skills_helpers.
Windsurf adopted the ``SKILL.md`` (Agent Skills) standard. Its user/global tool
dir is the NESTED ``~/.codeium/windsurf``; at project scope Cascade scans
``.windsurf`` plus the ``.agents`` cross-agent alias and (when Claude Code config
reading is enabled) ``.claude`` (docs-verified, docs.devin.ai/desktop/cascade/skills):

    User/global:  ~/.codeium/windsurf/skills/<name>/SKILL.md
                  ~/.agents/skills/<name>/SKILL.md    (compat)
                  ~/.claude/skills/<name>/SKILL.md    (compat, Claude-config-reading)
    Project:      <repo>/.windsurf/skills/<name>/SKILL.md
                  <repo>/.devin/skills/<name>/SKILL.md     (post-rebrand data folder)
                  <repo>/.agents/skills/<name>/SKILL.md    (compat)
                  <repo>/.claude/skills/<name>/SKILL.md    (compat, Claude-config-reading)

All of the above were verified against the SHIPPED APP, not the docs: the global
loader joins ``homeDir + codeiumDirPathSegments + "skills"`` and (when Claude
config reading is on) ``homeDir + ".claude" + "skills"`` and ``homeDir +
".agents" + "skills"``; the project watcher's predicate is quoted below.

``.github``/``.cursor``/``.codex``/``.cognition`` are NOT skill dirs — the app's
predicate does not include them — so they are intentionally excluded. Windsurf also reads
enterprise/system dirs (macOS ``/Library/Application Support/Windsurf/skills``,
Linux ``/etc/windsurf/skills``, Windows ``C:\\ProgramData\\Windsurf\\skills``);
these are outside the per-user/project walk and not collected here, matching how
other tools' system-scope skill dirs are handled. ``.claude`` is collected
unconditionally (its gating flag is not readable locally), matching the Cline
extractor precedent — overlap is de-duped downstream.

Like OpenCode, two parent-name sets are used because the user tool dir
(``.codeium/windsurf``) is nested and non-dotted at its leaf:

- ``WINDSURF_PARENT_DIR_NAMES`` (dot-form) drives the project WALK + project root
  resolution. It deliberately omits ``codeium``/``windsurf`` so the project walk
  never matches ``~/.codeium/windsurf`` and double-counts a user skill.
- ``WINDSURF_USER_PARENT_DIR_NAMES`` (with ``.codeium``) resolves user-level
  skills back to the owning home.

Windsurf has no plugin system, so skills carry ``source="standalone"``.
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

WINDSURF_DIR_NAME = ".windsurf"
DEVIN_DIR_NAME = ".devin"
CODEIUM_WINDSURF_USER_DIR = ".codeium/windsurf"
AGENTS_DIR_NAME = ".agents"
CLAUDE_DIR_NAME = ".claude"
SKILLS_DIR_NAME = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Project-level parent dirs (dot-form). Verified against the shipped app's own
# discovery predicate (Devin.app, sessions.desktop.main.js):
#
#     (p.includes("/.devin/skills/") || p.includes("/.windsurf/skills/")
#      || (claudeCodeConfigReadEnabled && p.includes("/.claude/skills/"))
#      || p.includes("/.agents/skills/")) && p.endsWith("/SKILL.md")
#
# ``.devin`` is the tool's NEW data folder (``dataFolderName: ".devin",
# oldDataFolderName: ".windsurf"``) after the Windsurf -> Devin rebrand; both are
# read. Drives the project walk + project root resolution. ``codeium``/``windsurf``
# (undotted) are NOT here so the walk never matches ``~/.codeium/windsurf``.
WINDSURF_PARENT_DIR_NAMES = (
    WINDSURF_DIR_NAME,
    DEVIN_DIR_NAME,
    AGENTS_DIR_NAME,
    CLAUDE_DIR_NAME,
)

# User-level dirs searched under each home. ``.codeium/windsurf`` is a nested path
# (pathlib joins it correctly); ``.agents`` + ``.claude`` are the compat aliases.
WINDSURF_USER_DIR_NAMES = (CODEIUM_WINDSURF_USER_DIR, AGENTS_DIR_NAME, CLAUDE_DIR_NAME)

# Parent-name set used ONLY to resolve user-level skills back to the owning home.
# ``.codeium`` lets the generic fallback map ``~/.codeium/windsurf/skills/x`` -> home;
# ``.agents``/``.claude`` resolve their user skills to the home directly.
WINDSURF_USER_PARENT_DIR_NAMES = (".codeium", AGENTS_DIR_NAME, CLAUDE_DIR_NAME)

# ──────────────────────────────────────────────────────────────────────────────
# Config-driven item type definitions
# ──────────────────────────────────────────────────────────────────────────────

WINDSURF_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name=SKILLS_DIR_NAME,
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)

WINDSURF_ITEM_CONFIGS = [WINDSURF_SKILL_CONFIG]

# ──────────────────────────────────────────────────────────────────────────────
# Windsurf-specific thin delegations to generic functions
# ──────────────────────────────────────────────────────────────────────────────


def find_windsurf_item_project_root(item_file: Path, config: ItemTypeConfig) -> Path:
    """Find the project root for a Windsurf project item file. Delegates to generic."""
    return find_item_project_root(item_file, config, parent_dir_names=WINDSURF_PARENT_DIR_NAMES)


def extract_windsurf_item_info(
    item_file: Path,
    extract_single_rule_file_func: Callable,
    scope: str,
    config: ItemTypeConfig,
) -> Optional[Dict]:
    """Extract information from a Windsurf project item file. Delegates to generic."""
    return extract_item_info(
        item_file, extract_single_rule_file_func, scope, config,
        parent_dir_names=WINDSURF_PARENT_DIR_NAMES,
    )


def extract_windsurf_items_from_directory(
    type_dir: Path,
    projects_by_root: Dict[str, List[Dict]],
    extract_single_rule_file_func: Callable,
    add_skill_func: Callable,
    config: ItemTypeConfig,
) -> None:
    """Extract all items of a given type from a Windsurf directory. Delegates to generic."""
    extract_items_from_directory(
        type_dir, projects_by_root, extract_single_rule_file_func, add_skill_func, config,
        parent_dir_names=WINDSURF_PARENT_DIR_NAMES,
    )


def extract_windsurf_user_level_items(
    user_home: Path,
    user_skills: List[Dict],
    extract_single_rule_file_func: Callable,
    configs: List[ItemTypeConfig],
) -> None:
    """Extract user-level Windsurf items from a user's home directory.

    Uses the ``.codeium/windsurf`` + ``.agents`` user dirs, and the
    ``.codeium``-aware parent set so ``~/.codeium/windsurf/skills`` resolves back
    to the home.
    """
    extract_user_level_items(
        user_home, user_skills, extract_single_rule_file_func, configs,
        user_dir_names=WINDSURF_USER_DIR_NAMES,
        parent_dir_names=WINDSURF_USER_PARENT_DIR_NAMES,
    )
