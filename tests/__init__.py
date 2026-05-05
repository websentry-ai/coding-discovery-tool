"""Stub the MCP scanner so config-parsing tests don't spawn real subprocesses."""

from unittest.mock import patch

patch(
    "scripts.coding_discovery_tools.mcp_extraction_helpers._scan_servers_in_mapping",
    return_value={},
).start()
