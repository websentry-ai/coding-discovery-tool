"""
Shared helper functions for Claude Code rules extraction.

This module contains OS-agnostic functions used by both macOS and Windows
Claude Code rules extractors to avoid code duplication.
"""

import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


def is_claude_md_file(filename: str) -> bool:
    """Check if filename is a CLAUDE.md file (case-insensitive)."""
    return filename.lower() == "claude.md"


def is_claude_local_md_file(filename: str) -> bool:
    """Check if filename is a CLAUDE.local.md file (case-insensitive)."""
    return filename.lower() == "claude.local.md"


def build_rules_project_list(projects_by_root: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Convert projects dictionary to list format with 'rules' key.

    Args:
        projects_by_root: Dictionary mapping project_root to list of rules

    Returns:
        List of project dicts with project_root and rules
    """
    return [
        {
            "project_root": project_root,
            "rules": rules
        }
        for project_root, rules in projects_by_root.items()
    ]


def extract_rules_from_rules_directory(
    rules_dir: Path,
    find_project_root_func,
    extract_and_add_func,
    projects_by_root: Dict[str, List[Dict]],
    scope: str = "project"
) -> None:
    """
    Extract all .md rule files from a .claude/rules/ directory recursively.

    Args:
        rules_dir: Path to the .claude/rules/ directory
        find_project_root_func: Function to find project root
        extract_and_add_func: Function to extract and add a rule file.
                              Signature: func(file_path, find_root_func, projects_by_root, scope=scope)
        projects_by_root: Dictionary to populate with rules
        scope: Scope of the rules
    """
    if not (rules_dir.exists() and rules_dir.is_dir()):
        return
    try:
        for md_file in rules_dir.rglob("*.md"):
            if md_file.is_file() and not md_file.name.startswith("."):
                extract_and_add_func(
                    md_file, find_project_root_func, projects_by_root, scope=scope
                )
    except Exception as e:
        logger.debug(f"Error extracting rules from {rules_dir}: {e}")
