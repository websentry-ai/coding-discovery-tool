"""
Roo Code rules extraction for Windows systems.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseRooRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_roo_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Roo Code rule file.
    """
    parent = rule_file.parent

    if parent.name == "rules" or parent.name.startswith("rules-"):
        if parent.parent.name == ".roo":
            return parent.parent.parent

    return parent


class WindowsRooRulesExtractor(BaseRooRulesExtractor):
    """Extractor for Roo Code rules on Windows systems."""

    ROO_RULES_DIRS = ["rules", "rules-architect", "rules-ask", "rules-code", "rules-debug", "rules-test"]

    def extract_all_roo_rules(self) -> List[Dict]:
        """
        Extract all Roo Code rules from all projects on Windows.
        """
        projects_by_root = {}
        self._extract_global_rules(projects_by_root)
        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Roo Code rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Roo Code rules from ~/.roo/rules/ and ~/.roo/rules-{mode}/.
        """
        user_home = Path.home()
        global_roo_dir = user_home / ".roo"

        if not global_roo_dir.exists() or not global_roo_dir.is_dir():
            return

        try:
            for rules_dir in global_roo_dir.iterdir():
                if not rules_dir.is_dir():
                    continue
                if rules_dir.name != "rules" and not rules_dir.name.startswith("rules-"):
                    continue

                for rule_file in rules_dir.glob("*.md"):
                    if rule_file.is_file():
                        rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing global .roo directory: {e}")
        except Exception as e:
            logger.debug(f"Error extracting global Roo Code rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects using optimized walker.
        """
        # Use the shared directory index: the drive is walked once for all
        # tools, not walked again by each tool.
        walk_for_tool_directories(
            root_path, root_path, ".roo",
            self._extract_rules_from_roo_directory, projects_by_root,
        )

    def _extract_rules_from_roo_directory(
        self, roo_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .roo directory.

        Searches for rules in:
        - .roo/rules/*.md
        - .roo/rules-architect/*.md
        - .roo/rules-ask/*.md
        - .roo/rules-code/*.md
        - .roo/rules-debug/*.md
        - .roo/rules-test/*.md
        - And any other .roo/rules-*/*.md directories
        """
        try:
            for item in roo_dir.iterdir():
                if item.is_dir() and (item.name == "rules" or item.name.startswith("rules-")):
                    for rule_file in item.glob("*.md"):
                        if rule_file.is_file():
                            rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing .roo directory {roo_dir}: {e}")
        except Exception as e:
            logger.debug(f"Error extracting rules from {roo_dir}: {e}")
