"""Test-package setup, imported by both pytest and `unittest discover` (CI).

Lives here, not conftest.py — `unittest discover` doesn't load conftest.
"""

import atexit
import os
import shutil
import tempfile
from unittest.mock import patch

# Stub the MCP scanner so config-parsing tests don't spawn real subprocesses.
patch(
    "scripts.coding_discovery_tools.mcp_extraction_helpers._scan_servers_in_mapping",
    return_value={},
).start()

# Hook discovery reads every enumerated home's hook configs; keep the developer's real ones out of
# tool-processing tests (hook extraction is tested with explicit homes in test_hooks_extraction).
patch("scripts.coding_discovery_tools.ai_tools_discovery._scan_user_homes", return_value=[]).start()

# Redirect the report retry queue to a throwaway temp file for the whole session
# so no test can touch the real /var/tmp queue. setdefault lets a test override it.
_queue_dir = tempfile.mkdtemp(prefix="ai-discovery-test-queue-")
atexit.register(shutil.rmtree, _queue_dir, ignore_errors=True)
os.environ.setdefault(
    "AI_DISCOVERY_QUEUE_FILE",
    os.path.join(_queue_dir, "ai-discovery-queue.json"),
)
