"""Test-package setup that runs under BOTH ``pytest`` and ``unittest discover``.

``unittest discover`` (used by CI) imports the ``tests`` package -- i.e. this
file -- but never loads ``conftest.py``, so any session-wide setup that must
also hold in CI lives here, not in conftest.

1. Stub the MCP scanner so config-parsing tests don't spawn real subprocesses.
2. Redirect the report retry queue to a throwaway temp file for the whole
   session (see below).
"""

import os
import tempfile
from unittest.mock import patch

patch(
    "scripts.coding_discovery_tools.mcp_extraction_helpers._scan_servers_in_mapping",
    return_value={},
).start()

# Load-bearing isolation. The discovery tool persists failed report envelopes to
# a per-UID file under /var/tmp (utils._get_queue_file_path). A test that wrote a
# fixture there and was interrupted before teardown could be drained and POSTed
# to production by a later real agent run -- this is how the phantom
# "QUEUED-DEVICE" device reached the dashboard. Point the queue at a throwaway
# temp file for the ENTIRE session, before any test runs or CLI subprocess is
# spawned (subprocesses inherit this env). setdefault lets an individual test
# override it per-test.
os.environ.setdefault(
    "AI_DISCOVERY_QUEUE_FILE",
    os.path.join(
        tempfile.mkdtemp(prefix="ai-discovery-test-queue-"),
        "ai-discovery-queue.json",
    ),
)
