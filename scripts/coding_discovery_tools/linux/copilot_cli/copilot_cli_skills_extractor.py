"""
GitHub Copilot CLI skills extraction for Linux systems.

Subclasses the macOS extractor, overriding:

  - ``_extract_user_level_skills``   → iterates ``get_linux_user_homes()``
  - ``_extract_project_level_skills`` → uses Linux ``get_top_level_directories``
  - ``_walk_for_skills``             → uses Linux ``should_skip_path`` /
    ``should_skip_system_path`` instead of the macOS module-level references
    in the base class, so ``/home`` is not filtered out during the recursive walk.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...claude_code_skills_helpers import add_skill_to_project, is_user_level_claude_subdir
from ...constants import MAX_SEARCH_DEPTH
from ...copilot_cli_skills_helpers import (
    COPILOT_CLI_ITEM_CONFIGS,
    COPILOT_CLI_PARENT_DIR_NAMES,
    extract_copilot_cli_items_from_directory,
    extract_copilot_cli_user_level_items,
)
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.copilot_cli_skills_extractor import MacOSCopilotCliSkillsExtractor
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    should_process_directory,
)

logger = logging.getLogger(__name__)


class LinuxCopilotCliSkillsExtractor(MacOSCopilotCliSkillsExtractor):
    """Extractor for GitHub Copilot CLI skills on Linux systems."""

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level skills from ~/.copilot/skills and ~/.agents/skills.

        Scans every home returned by ``get_linux_user_homes()``.
        """
        for user_home in get_linux_user_homes():
            try:
                extract_copilot_cli_user_level_items(
                    user_home, user_skills, extract_single_rule_file, COPILOT_CLI_ITEM_CONFIGS
                )
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping {user_home}: {exc}")

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level skills from ``/`` on Linux using Linux top-level dirs."""
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as exc:
                logger.warning(f"Error accessing root directory: {exc}")
                for user_home in get_linux_user_homes():
                    try:
                        self._walk_for_skills(user_home, user_home, projects_by_root, current_depth=0)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {user_home}: {e}")
        else:
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recursively walk for skills dirs using Linux skip helpers.

        Overrides the inherited macOS walk to replace the module-level macOS
        ``should_skip_system_path`` reference (which excludes ``/home``) with
        the Linux version.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
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
