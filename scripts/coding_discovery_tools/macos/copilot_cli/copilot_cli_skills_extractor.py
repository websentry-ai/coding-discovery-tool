"""
GitHub Copilot CLI skills extraction for macOS systems.

For the standalone ``@github/copilot`` CLI. Extracts agent skills (each a
subdirectory containing a ``SKILL.md``), grouping project skills by project root.

User/global skills:  ~/.copilot/skills/<name>/SKILL.md
                     ~/.agents/skills/<name>/SKILL.md
Project skills:      **/.github/skills/<name>/SKILL.md
                     **/.claude/skills/<name>/SKILL.md
                     **/.agents/skills/<name>/SKILL.md

Mirrors the Cline/Cursor skills extractors (macOS base + a threaded Windows
subclass) and reuses the shared config-driven engine in
``claude_code_skills_helpers`` via ``copilot_cli_skills_helpers``.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCopilotCliSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
    is_running_as_root,
    scan_user_directories,
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


class MacOSCopilotCliSkillsExtractor(BaseCopilotCliSkillsExtractor):
    """Extractor for GitHub Copilot CLI skills on macOS systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract all GitHub Copilot CLI skills from all projects on macOS.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope "user")
            - project_skills: List of {project_root, skills[]} project dicts
        """
        user_skills: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_skills(user_skills)

        root_path = Path("/")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level skills from ~/.copilot/skills and ~/.agents/skills.

        Scans every user's home when running as root, else the current user.
        """
        def extract_for_user(user_home: Path) -> None:
            extract_copilot_cli_user_level_items(
                user_home, user_skills, extract_single_rule_file, COPILOT_CLI_ITEM_CONFIGS
            )

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level skills (.github / .claude / .agents) from the filesystem root."""
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search for Copilot CLI skills")
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
        """Recursively walk looking for ``.github``/``.claude``/``.agents`` skills dirs.

        Symlinked dirs are skipped; user-home tool dirs (handled as user scope) are
        not double-counted as project skills; depth is bounded; never crashes.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    # Skip other-tool config dirs (e.g. ~/.antigravity/extensions/<pkg>)
                    # but still allow the shared .claude/.agents skill dirs a real repo
                    # root legitimately carries (handled below as collection targets).
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
                                    # is_user_level_claude_subdir works generically for any tool dir:
                                    # a user-home ~/.copilot|.agents|.claude is handled as user scope.
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
