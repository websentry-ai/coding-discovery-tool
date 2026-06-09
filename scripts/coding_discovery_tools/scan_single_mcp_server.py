#!/usr/bin/env python3
"""
Scan ONE MCP server on demand and report it to the control plane.

This is the fast path: when the gateway sees an MCP tool call for a server whose
fingerprint it doesn't recognise yet, it tells the client (via the PreToolUse
hook) to scan just that one server. This script does exactly that — it reuses the
normal discovery scanner (`transform_mcp_servers_to_array`, including its OAuth
token injection and env/header stripping), then POSTs the result to the dedicated
single-server endpoint, which upserts MCPServerMetadata and kicks canonicalisation.
So a newly added MCP server becomes policy-ready in minutes instead of waiting for
the daily full scan.

Unlike the full discovery report this sends NO device / project / installation
data — the endpoint writes metadata only (keyed by fingerprint), so no device_id
is needed. `env` / `headers` are stripped from the payload by the scanner.

All HTTP uses curl (never urllib) per the Zscaler constraint in CLAUDE.md.

Usage:
  scan_single_mcp_server.py --name linear \\
      --server-json '{"url": "https://mcp.linear.app/sse"}' \\
      --domain https://api.example.com [--api-key KEY]

The API key falls back to the UNBOUND_API_KEY environment variable.
"""
import argparse
import json
import os
import subprocess
import sys

try:
    from coding_discovery_tools.mcp_extraction_helpers import transform_mcp_servers_to_array
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from coding_discovery_tools.mcp_extraction_helpers import transform_mcp_servers_to_array

REPORT_PATH = "/api/v1/ai-tools/mcp-server-scan/"


def _normalize_url(url):
    return (url or "").rstrip("/")


def scan_one(server_name, server_config):
    """Scan a single server; returns the per-server object {name, command, url, args, scan}."""
    servers = transform_mcp_servers_to_array({server_name: server_config})
    return servers[0] if servers else None


def report(domain, api_key, server_obj):
    """POST the scanned server to the single-server endpoint. Returns the curl result."""
    url = f"{_normalize_url(domain)}{REPORT_PATH}"
    payload = json.dumps({"mcp_server": server_obj})
    return subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            "-H", f"Authorization: Bearer {api_key}",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: AI-Tools-Discovery/1.0",
            "-d", payload,
            "--max-time", "60",
            "-w", "\n%{http_code}",
            url,
        ],
        capture_output=True, text=True, timeout=90,
    )


def main():
    parser = argparse.ArgumentParser(description="Scan one MCP server and report it.")
    parser.add_argument("--name", required=True, help="MCP server name (as configured)")
    parser.add_argument("--server-json", required=True,
                        help='JSON server config, e.g. {"command":..,"args":..,"url":..,"type":..}')
    parser.add_argument("--domain", required=True, help="Control-plane base URL")
    parser.add_argument("--api-key", default=os.environ.get("UNBOUND_API_KEY"),
                        help="Discovery/gateway API key (defaults to UNBOUND_API_KEY env)")
    args = parser.parse_args()

    if not args.api_key:
        print("error: no api key (pass --api-key or set UNBOUND_API_KEY)", file=sys.stderr)
        return 2

    try:
        server_config = json.loads(args.server_json)
    except (ValueError, TypeError) as e:
        print(f"error: invalid --server-json: {e}", file=sys.stderr)
        return 2
    if not isinstance(server_config, dict):
        print("error: --server-json must be a JSON object", file=sys.stderr)
        return 2

    try:
        server_obj = scan_one(args.name, server_config)
    except Exception as e:
        print(f"error: scan failed: {e}", file=sys.stderr)
        return 1
    if not server_obj:
        print("error: scan produced no result", file=sys.stderr)
        return 1

    try:
        result = report(args.domain, args.api_key, server_obj)
    except Exception as e:
        print(f"error: report failed: {e}", file=sys.stderr)
        return 1

    out = (result.stdout or "").strip()
    http_code = out.rsplit("\n", 1)[-1] if out else ""
    if http_code.startswith("2"):
        return 0
    print(f"error: report failed (http {http_code}): {out[:300]} {(result.stderr or '')[:200]}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
