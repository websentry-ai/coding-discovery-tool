"""
Shared helper functions for Cursor rules extraction.

This module contains OS-agnostic functions used by both macOS and Windows
Cursor rules extractors to avoid code duplication.
"""


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
