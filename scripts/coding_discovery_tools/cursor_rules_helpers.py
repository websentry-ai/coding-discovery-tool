"""
Shared helper functions for Cursor rules extraction.

This module contains OS-agnostic functions used by both macOS and Windows
Cursor rules extractors to avoid code duplication.
"""

from pathlib import Path
from typing import Dict, List


def is_cursor_rule_md_file(filename: str) -> bool:
    """
    Check if filename is a Cursor rule .md file.

    Matches .md files that are not hidden (dot-prefixed) and not AGENTS.md,
    to prevent AGENTS.md from being detected as both a rule and an agent
    instruction file.

    Args:
        filename: The filename to check

    Returns:
        True if the filename is a valid Cursor rule .md file
    """
    return (
        filename.lower().endswith(".md")
        and not filename.startswith(".")
        and not is_agents_md_file(filename)
    )


def is_agents_md_file(filename: str) -> bool:
    """
    Check if filename is an AGENTS.md file (case-insensitive).

    Args:
        filename: The filename to check

    Returns:
        True if the filename matches AGENTS.md (case-insensitive)
    """
    return filename.lower() == "agents.md"


def _extract_files_from_dir(
    directory: Path,
    extract_func,
    find_root_func,
    add_func,
    projects_by_root: Dict[str, List[Dict]],
    project_root_str: str,
    scope: str
) -> None:
    """Extract .mdc and .md rule files from a single directory."""
    for mdc_file in directory.glob("*.mdc"):
        rule_info = extract_func(mdc_file, find_root_func, scope=scope)
        if rule_info:
            add_func(rule_info, project_root_str, projects_by_root)
    for md_file in directory.glob("*.md"):
        if is_cursor_rule_md_file(md_file.name):
            rule_info = extract_func(md_file, find_root_func, scope=scope)
            if rule_info:
                add_func(rule_info, project_root_str, projects_by_root)


def extract_cursor_rules_from_dir(
    base_dir: Path,
    extract_func,
    find_root_func,
    add_func,
    projects_by_root: Dict[str, List[Dict]],
    project_root_str: str,
    scope: str = "user"
) -> None:
    """
    Extract .mdc and .md rule files from a cursor directory and its rules/ subdirectory.

    Args:
        base_dir: Path to the cursor directory (e.g., ~/.cursor/)
        extract_func: Function to extract a single rule file
        find_root_func: Function to find project root
        add_func: Function to add rule to project dict
        projects_by_root: Dictionary to populate with rules
        project_root_str: Project root path string for these rules
        scope: Rule scope ("user" or "project")
    """
    _extract_files_from_dir(
        base_dir, extract_func, find_root_func, add_func,
        projects_by_root, project_root_str, scope
    )
    rules_dir = base_dir / "rules"
    if rules_dir.exists() and rules_dir.is_dir():
        _extract_files_from_dir(
            rules_dir, extract_func, find_root_func, add_func,
            projects_by_root, project_root_str, scope
        )
