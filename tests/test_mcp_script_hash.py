"""
Tests for local-script MCP fingerprinting in mcp_script_hash.

A server that runs a local script gets `scriptHash` (sha256 of the file the
gateway re-hashes) plus `script_content` (base64 body for the classifier). A
server that runs a package/binary/url gets neither. The hash must match a plain
sha256 of the bytes so the backend's re-hash lines up.
"""

import base64
import hashlib
import os
import tempfile
import unittest

from scripts.coding_discovery_tools.mcp_script_hash import (
    augment_script_fields,
    compute_script_hash,
)

_BODY = b"#!/usr/bin/env python3\nfrom mcp.server.fastmcp import FastMCP\nmcp = FastMCP('x')\n"


class TestAugmentScriptFields(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "wb") as fh:
            fh.write(_BODY)
        self.addCleanup(os.unlink, self.path)

    def test_local_script_gets_hash_and_body(self):
        server_obj = {"command": "python3", "args": [self.path]}
        augment_script_fields(server_obj)

        expected_hash = hashlib.sha256(_BODY).hexdigest()
        self.assertEqual(server_obj["scriptHash"], expected_hash)
        self.assertEqual(
            base64.b64decode(server_obj["script_content"]), _BODY,
            "script_content must be the base64 of the exact hashed bytes",
        )

    def test_command_is_script_file_itself(self):
        server_obj = {"command": self.path}
        augment_script_fields(server_obj)
        self.assertEqual(server_obj["scriptHash"], hashlib.sha256(_BODY).hexdigest())

    def test_package_command_gets_nothing(self):
        server_obj = {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]}
        augment_script_fields(server_obj)
        self.assertNotIn("scriptHash", server_obj)
        self.assertNotIn("script_content", server_obj)

    def test_binary_without_script_extension_gets_nothing(self):
        server_obj = {"command": "/usr/local/bin/some-mcp-binary", "args": []}
        augment_script_fields(server_obj)
        self.assertNotIn("scriptHash", server_obj)

    def test_missing_file_yields_no_hash(self):
        self.assertIsNone(
            compute_script_hash("python3", ["/no/such/file/here.py"], None)
        )

    def test_relative_path_without_cwd_is_skipped(self):
        # Discovery calls with cwd=None; a relative script can't be resolved and
        # must not be hashed against the scanner's own working directory.
        self.assertIsNone(compute_script_hash("python3", ["server.py"], None))

    def test_relative_arg_resolves_via_server_config_cwd(self):
        # A relative script arg paired with the config's own cwd must still be
        # fingerprinted (resolved against that cwd, not the scanner's).
        directory, base = os.path.split(self.path)
        server_obj = {"command": "python3", "args": [base], "cwd": directory}
        augment_script_fields(server_obj)
        self.assertEqual(server_obj["scriptHash"], hashlib.sha256(_BODY).hexdigest())


if __name__ == "__main__":
    unittest.main()
