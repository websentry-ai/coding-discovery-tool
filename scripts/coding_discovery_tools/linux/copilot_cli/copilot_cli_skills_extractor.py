"""
GitHub Copilot CLI skills extraction for Linux systems.

For the standalone ``@github/copilot`` CLI. Extracts agent skills (each a
subdirectory containing a ``SKILL.md``), grouping project skills by project root.

User/global skills:  ~/.copilot/skills/<name>/SKILL.md
                     ~/.agents/skills/<name>/SKILL.md
Project skills:      **/.github/skills/<name>/SKILL.md
                     **/.claude/skills/<name>/SKILL.md
                     **/.agents/skills/<name>/SKILL.md

DRY: the per-tool config (``COPILOT_CLI_ITEM_CONFIGS``) and the project-grouping
helpers are inherited from ``MacOSCopilotCliSkillsExtractor``. Only the OS
primitives differ — the all-users scan, the filesystem root + top-level
enumeration, and the skip predicate (the Linux ``should_skip_system_path`` must
NOT skip ``/home``, unlike the macOS one) — so the three methods that touch those
primitives are overridden here with ``linux_extraction_helpers`` equivalents.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir
from ...linux_extraction_helpers import (
    extract_single_rule_file,
    get_linux_user_homes,
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
)
from ...copilot_cli_skills_helpers import (
    COPILOT_CLI_PARENT_DIR_NAMES,
    COPILOT_CLI_ITEM_CONFIGS,
    extract_copilot_cli_items_from_directory,
    extract_copilot_cli_user_level_items,
)
from ...claude_code_skills_helpers import (
    add_skill_to_project,
    is_user_level_claude_subdir,
)
from ...macos.copilot_cli.copilot_cli_skills_extractor import (
    MacOSCopilotCliSkillsExtractor,
)

logger = logging.getLogger(__name__)


class LinuxCopilotCliSkillsExtractor(MacOSCopilotCliSkillsExtractor):
    """Extractor for GitHub Copilot CLI skills on Linux systems."""

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level skills from ~/.copilot/skills and ~/.agents/skills
        for every Linux user home (all users when root, else the current user)."""
        for user_home in get_linux_user_homes():
            try:
                extract_copilot_cli_user_level_items(
                    Path(user_home), user_skills, extract_single_rule_file, COPILOT_CLI_ITEM_CONFIGS
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level skills from the filesystem root, using the Linux
        top-level enumeration (which includes ``/home``)."""
        if root_path == Path("/"):
            try:
                for dir_path in get_top_level_directories(root_path):
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                home_path = Path.home()
                self._walk_for_skills(home_path, home_path, projects_by_root, current_depth=0)
        else:
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recursively walk for ``.github``/``.claude``/``.agents`` skills dirs.

        Identical to the macOS walk but uses the Linux ``should_skip_system_path``
        (which does NOT skip ``/home``, so project skills under user homes are not
        silently dropped). Symlinked dirs are skipped; user-home tool dirs are not
        double-counted as project skills; depth is bounded; never crashes.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if (
                        should_skip_path(item)
                        or should_skip_system_path(item)
                        or traverses_other_tool_config_dir(item, allow=SHARED_SKILL_DIRS)
                    ):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name in COPILOT_CLI_PARENT_DIR_NAMES:
                            for config in COPILOT_CLI_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir():
                                    if not is_user_level_claude_subdir(type_dir):
                                        extract_copilot_cli_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            add_skill_to_project,
                                            config,
                                        )
                            continue

                        if item.is_symlink():
                            continue

                        self._walk_for_skills(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")
