"""
Keep tests independent of the machine running them.

process_single_tool reads hook configs from every real user home on the
machine; without this, a developer's own ~/.cursor/hooks.json (or any other
hook file) would leak into tests that assert on a tool's projects.
Hook extraction itself is tested with explicit homes in test_hooks_extraction.py.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_user_homes_for_hooks(monkeypatch):
    monkeypatch.setattr("scripts.coding_discovery_tools.ai_tools_discovery._scan_user_homes", lambda: [])
