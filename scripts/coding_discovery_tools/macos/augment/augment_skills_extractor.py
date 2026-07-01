"""
Augment Code skills/commands extraction for macOS systems.

Extracts agent skills (each a subdirectory containing a ``SKILL.md``) and slash
commands (flat ``.md`` files), grouping project items by project root.

User/global:  ~/.augment/skills/<name>/SKILL.md  and  ~/.augment/commands/*.md
Project:      **/.augment/skills/<name>/SKILL.md  and  **/.augment/commands/*.md

Reuses the shared config-driven engine in ``claude_code_skills_helpers`` via
``augment_skills_helpers``. Augment has no plugin system, so every item is
``source="standalone"``.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseAugmentSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, traverses_other_tool_config_dir
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    get_top_level_directories,
    is_running_as_root,
    scan_user_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
)
from ...augment_skills_helpers import (
    AUGMENT_PARENT_DIR_NAMES,
    AUGMENT_ITEM_CONFIGS,
    extract_augment_items_from_directory,
    extract_augment_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class MacOSAugmentSkillsExtractor(BaseAugmentSkillsExtractor):
    """Extractor for Augment Code skills/commands on macOS systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract all Augment Code skills/commands from all projects on macOS.

        Returns:
            Dict with:
            - user_skills: List of user-level skill/command dicts (scope "user")
            - project_skills: List of {project_root, skills[]} project dicts
        """
        user_skills: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_skills(user_skills)
        self._extract_project_level_skills(self._filesystem_root(), projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level items from ~/.augment/skills and ~/.augment/commands."""
        def extract_for_user(user_home: Path) -> None:
            extract_augment_user_level_items(
                user_home, user_skills, self._extract_single_rule_file, AUGMENT_ITEM_CONFIGS
            )

        self._scan_all_user_homes(extract_for_user)

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level ``.augment`` skills/commands from the root."""
        if root_path == self._filesystem_root():
            try:
                for dir_path in self._iter_top_level_dirs(root_path):
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search for Augment skills")
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
        """Recursively walk looking for ``.augment`` skills/commands dirs.

        Symlinked dirs are skipped; user-home ``~/.augment`` (handled as user
        scope) is not double-counted; depth is bounded; never crashes.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if self._should_skip_walk_item(item):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    # Skip non-dirs and symlinked dirs BEFORE the .augment
                    # handling / recursion (mirrors the rules + mcp + settings
                    # walk ordering) so a symlinked .augment can't be followed.
                    if not item.is_dir() or item.is_symlink():
                        continue

                    if item.name in AUGMENT_PARENT_DIR_NAMES:
                        for config in AUGMENT_ITEM_CONFIGS:
                            type_dir = item / config.dir_name
                            # Guard the skills/commands subdir against symlinks too
                            # (mirrors the parent .augment guard above): under a
                            # root MDM scan a user could point .augment/skills at an
                            # arbitrary dir and have the scanner traverse it.
                            if (
                                type_dir.exists()
                                and type_dir.is_dir()
                                and not type_dir.is_symlink()
                            ):
                                if not self._is_user_level_skill_dir(type_dir):
                                    extract_augment_items_from_directory(
                                        type_dir,
                                        projects_by_root,
                                        self._extract_single_rule_file,
                                        add_skill_to_project,
                                        config,
                                    )
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

    # -- OS-specific seams (overridden by the Windows/Linux subclasses) -------

    def _extract_single_rule_file(self, *args, **kwargs):
        """Seam: the OS-specific ``extract_single_rule_file`` (file metadata read)."""
        return extract_single_rule_file(*args, **kwargs)

    def _should_skip_walk_item(self, item: Path) -> bool:
        """Seam: whether the project walk skips ``item`` (system/skip/other-tool)."""
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every user home (all users when root)."""
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _filesystem_root(self) -> Path:
        """Root the project walk starts from (POSIX ``/`` on macOS)."""
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under the filesystem root, system dirs excluded."""
        return list(get_top_level_directories(root_path))

    def _is_user_level_skill_dir(self, type_dir: Path) -> bool:
        """Whether ``type_dir`` (e.g. ``<home>/.augment/skills``) is a *user-level*
        dir the project walk must skip — already reported as user scope."""
        return is_user_level_claude_subdir(type_dir)
