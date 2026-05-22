"""Tests for the _metric_slug helper used in per-tool Sentry metric keys."""
import pytest

from scripts.coding_discovery_tools.ai_tools_discovery import _metric_slug


@pytest.mark.parametrize("input_name,expected", [
    ("Claude Code", "claude_code"),
    ("Cursor CLI", "cursor_cli"),
    ("GitHub Copilot VS Code", "github_copilot_vs_code"),
    ("JetBrains IDEs", "jetbrains_ides"),
    ("", "unknown"),
    ("   ", "___"),
    ("123-tool", "_123-tool"),
    ("A" * 100, "a" * 50),
    ("hello.world", "hello.world"),
    ("with-dash", "with-dash"),
    ("special!@#chars", "special___chars"),
])
def test_metric_slug(input_name, expected):
    assert _metric_slug(input_name) == expected


def test_metric_slug_returns_valid_sentry_key():
    """Result must match Sentry pattern: [a-zA-Z_][a-zA-Z0-9_.\\-]*"""
    import re
    pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.\-]*$')
    names = ["Claude Code", "Cursor", "123", "---", "a", "JetBrains IDEs", "VS Code"]
    for name in names:
        slug = _metric_slug(name)
        assert pattern.match(slug), f"_metric_slug({name!r}) = {slug!r} doesn't match Sentry pattern"
