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

    def test_secret_in_path_segment_is_redacted(self):
        # A token embedded as a path segment (webhook-style) is a credential
        # just as much as one in the query, and must not be collected.
        token = "Xk9mP2qR7wL4tN8vB3cY6dF1gH5jS0dQ2aW"
        reported = self._report({
            "pathed": {
                "type": "http",
                "url": f"https://mcp.pathed.com/mcp/{token}",
            }
        })
        url = reported["pathed"]["url"]
        self.assertNotIn(token, url)
        # The route leading to the secret survives so the destination is named.
        self.assertEqual(url, "https://mcp.pathed.com/mcp/<redacted>")

    def test_uuid_path_segment_is_redacted(self):
        # A UUID session token is the common shape for hosted MCP endpoints; its
        # hyphens split it into 12-char runs, so it must be matched by shape.
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        reported = self._report({
            "n8n": {"type": "http", "url": f"https://mcp.host.com/mcp/{uuid}"},
        })
        url = reported["n8n"]["url"]
        self.assertNotIn(uuid, url)
        self.assertEqual(url, "https://mcp.host.com/mcp/<redacted>")

    def test_numeric_token_path_segment_is_redacted(self):
        # A digits-only token can never clear the entropy floor, so it is judged
        # on length once long enough.
        token = "019283746501928374650192"
        reported = self._report({
            "num": {"type": "http", "url": f"https://mcp.host.com/mcp/{token}"},
        })
        url = reported["num"]["url"]
        self.assertNotIn(token, url)
        self.assertEqual(url, "https://mcp.host.com/mcp/<redacted>")

    def test_short_route_segments_are_kept(self):
        # Ordinary route segments are not secrets and must survive untouched.
        for route in ("https://mcp.route.com/v1/sse",
                      "https://mcp.route.com/mcp",
                      "https://mcp.route.com/sse",
                      "https://mcp.route.com/mcp/messages"):
            reported = self._report({"r": {"type": "http", "url": route}})
            self.assertEqual(reported["r"]["url"], route)

    def test_multi_tenant_path_is_preserved(self):
        # A multi-tenant proxy path disambiguates on the full path; its human
        # segments are not high-entropy secrets and must be kept.
        url = "https://proxy.corp.com/tenant-acme/us-east-1/mcp"
        reported = self._report({"t": {"type": "http", "url": url}})
        self.assertEqual(reported["t"]["url"], url)


if __name__ == "__main__":
    unittest.main()
