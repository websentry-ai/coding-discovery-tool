"""A remote MCP server's credential can live in its url — userinfo in the
authority, or a token in the query — not only in env and headers. Discovery
already drops env and headers; the reported url must be redacted the same way
so the secret is never collected off the machine.

The scan pass is stubbed out, so no test reaches the network.
"""
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    transform_mcp_servers_to_array,
)


class TestMcpUrlCredentialRedaction(unittest.TestCase):

    def _report(self, servers):
        scans = {name: {"tools": [], "error": None} for name in servers}
        with patch(
            "scripts.coding_discovery_tools.mcp_extraction_helpers."
            "_scan_servers_in_mapping",
            return_value=scans,
        ):
            reported = transform_mcp_servers_to_array(
                servers, _skip_script_augmentation=True
            )
        return {server["name"]: server for server in reported}

    def test_userinfo_in_authority_is_stripped(self):
        reported = self._report({
            "vendor": {
                "type": "http",
                "url": "https://alice:s3cr3t@mcp.vendor.com/v1/sse",
            }
        })
        url = reported["vendor"]["url"]
        self.assertNotIn("s3cr3t", url)
        self.assertNotIn("alice", url)
        self.assertNotIn("@", url)
        # Non-secret parts survive so the destination is still named.
        self.assertEqual(url, "https://mcp.vendor.com/v1/sse")

    def test_token_query_params_are_stripped(self):
        reported = self._report({
            "hosted": {
                "type": "http",
                "url": "https://mcp.hosted.com/sse?token=abc123&api_key=zzz",
            }
        })
        url = reported["hosted"]["url"]
        self.assertNotIn("abc123", url)
        self.assertNotIn("zzz", url)
        self.assertNotIn("?", url)
        self.assertEqual(url, "https://mcp.hosted.com/sse")

    def test_clean_url_is_unchanged(self):
        reported = self._report({
            "plain": {
                "type": "http",
                "url": "https://mcp.plain.com/v1/sse",
            }
        })
        self.assertEqual(reported["plain"]["url"], "https://mcp.plain.com/v1/sse")


if __name__ == "__main__":
    unittest.main()
