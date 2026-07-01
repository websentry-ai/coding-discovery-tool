"""
GitHub Copilot CLI skills extraction for Windows systems.

For the standalone ``@github/copilot`` CLI. Extracts agent skills (each a
subdirectory containing a ``SKILL.md``), grouping project skills by project root.

User/global skills:  ~/.copilot/skills/<name>/SKILL.md
                     ~/.agents/skills/<name>/SKILL.md
Project skills:      **/.github/skills/<name>/SKILL.md
                     **/.claude/skills/<name>/SKILL.md
                     **/.agents/skills/<name>/SKILL.md

NOTE: unlike the sibling Copilot CLI rules/MCP/settings extractors (which use a
macOS-base + thin-Windows-subclass), this follows the skills-family structure —
a standalone Windows class with a ThreadPoolExecutor walk — matching
``WindowsClineSkillsExtractor`` / ``WindowsCursorSkillsExtractor`` (threading is
the established skills perf pattern; the shared engine absorbs the per-tool logic).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCopilotCliSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)
from ...copilot_cli_skills_helpers import (
    COPILOT_CLI_PARENT_DIR_NAMES,
    COPILOT_CLI_ITEM_CONFIGS,
    extract_copilot_cli_items_from_directory,
    extract_copilot_cli_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class WindowsCopilotCliSkillsExtractor(BaseCopilotCliSkillsExtractor):
    """Extractor for GitHub Copilot CLI skills on Windows systems."""

    def __init__(self):
        """Initialize the extractor with thread synchronization."""
        super().__init__()
        self._lock = threading.Lock()
        self._users_directory = str(Path.home().parent)

    def extract_all_skills(self) -> Dict:
        """
        Extract all GitHub Copilot CLI skills from all projects on Windows.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope "user")
            - project_skills: List of {project_root, skills[]} project dicts
        """
        user_skills: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_skills(user_skills)

        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Copilot CLI skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level skills from ~/.copilot/skills and ~/.agents/skills.

        ``scan_windows_user_directories`` scans all C:\\Users users when admin,
        else the current user.
        """
        def extract_for_user(user_home: Path) -> None:
            extract_copilot_cli_user_level_items(
                user_home, user_skills, extract_single_rule_file, COPILOT_CLI_ITEM_CONFIGS
            )

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level skills from the drive root, parallelized over top-level dirs."""
        try:
            top_level_dirs = [
                item for item in root_path.iterdir()
                if item.is_dir() and not should_skip_path(item, get_windows_system_directories())
            ]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_skills, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recursively walk looking for ``.github``/``.claude``/``.agents`` skills dirs."""
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    # Skip other-tool config dirs (e.g. installed extension packages),
                    # but allow the shared .claude/.agents skill dirs a real repo root
                    # legitimately carries (collected below as targets).
                    if (
                        should_skip_path(item, get_windows_system_directories())
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
                                    if not is_user_level_claude_subdir(type_dir, self._users_directory):
                                        extract_copilot_cli_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            self._add_skill_to_project_threadsafe,
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

    def _add_skill_to_project_threadsafe(
        self,
        skill_info: Dict,
        project_root: str,
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        """Thread-safe wrapper around ``add_skill_to_project`` for the executor walk."""
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
