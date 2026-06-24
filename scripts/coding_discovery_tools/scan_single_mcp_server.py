#!/usr/bin/env python3
"""Scan ONE MCP server on demand and report it to the control plane.

Reuses the discovery scanner for a single server, then POSTs to the
single-server endpoint, which writes metadata only (keyed by fingerprint, no
device/project) and kicks canonicalisation. HTTP uses curl per the Zscaler
constraint in CLAUDE.md.
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
    obj = servers[0] if servers else None
    # Forward the base64 script body the hook attached for local-script servers
    # (it resolved the path with cwd; we run detached without it). The backend
    # recomputes sha256 -> `script:<hash>` fingerprint and stores the body.
    if obj is not None and isinstance(server_config, dict) and server_config.get('script_content'):
        obj['script_content'] = server_config['script_content']
        print(f"info: forwarding script_content ({len(server_config['script_content'])} b64 chars) for {server_name}",
              file=sys.stderr)
    return obj


def _curl_config_quote(value):
    """Escape a value for a curl --config double-quoted field."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def report(domain, api_key, server_obj):
    """POST the scanned server to the single-server endpoint. Returns the curl result.

    The bearer token and the JSON body (whose `args` can carry credentials) are
    both fed to curl through a config on stdin (`--config -`), so neither lands in
    argv / /proc/<pid>/cmdline. Using stdin rather than a temp file also avoids
    Windows temp-dir resolution issues when launched under Git Bash.
    """
    url = f"{_normalize_url(domain)}{REPORT_PATH}"
    payload = json.dumps({"mcp_server": server_obj})
    curl_config = (
        f'header = "Authorization: Bearer {_curl_config_quote(api_key)}"\n'
        'header = "Content-Type: application/json"\n'
        'header = "User-Agent: AI-Tools-Discovery/1.0"\n'
        f'data = "{_curl_config_quote(payload)}"\n'
    )
    return subprocess.run(
        [
            "curl", "-s", "-X", "POST", "--config", "-",
            "--max-time", "60",
            "-w", "\n%{http_code}",
            url,
        ],
        input=curl_config,
        capture_output=True, text=True, timeout=90,
    )


def main():
    parser = argparse.ArgumentParser(description="Scan one MCP server and report it.")
    parser.add_argument("--name", required=True, help="MCP server name (as configured)")
    # Prefer the env var so the config (which may carry secrets in `args`) never
    # appears in the process argv / /proc/<pid>/cmdline.
    parser.add_argument("--server-json", default=os.environ.get("UNBOUND_MCP_SERVER_JSON"),
                        help='JSON server config (defaults to the UNBOUND_MCP_SERVER_JSON env)')
    parser.add_argument("--domain", required=True, help="Control-plane base URL")
    parser.add_argument("--api-key", default=os.environ.get("UNBOUND_API_KEY"),
                        help="Discovery/gateway API key (defaults to UNBOUND_API_KEY env)")
    args = parser.parse_args()
    ctx = f"server={args.name!r} domain={args.domain!r}"

    if not args.api_key:
        print(f"error: no api key (pass --api-key or set UNBOUND_API_KEY) [{ctx}]", file=sys.stderr)
        return 2
    if not args.server_json:
        print(f"error: no server config (pass --server-json or set UNBOUND_MCP_SERVER_JSON) [{ctx}]",
              file=sys.stderr)
        return 2

    try:
        server_config = json.loads(args.server_json)
    except (ValueError, TypeError) as e:
        print(f"error: invalid server config json: {e} [{ctx}]", file=sys.stderr)
        return 2
    if not isinstance(server_config, dict):
        print(f"error: server config must be a JSON object [{ctx}]", file=sys.stderr)
        return 2

    try:
        server_obj = scan_one(args.name, server_config)
    except Exception as e:
        print(f"error: scan failed: {e} [{ctx}]", file=sys.stderr)
        return 1
    if not server_obj:
        print(f"error: scan produced no result [{ctx}]", file=sys.stderr)
        return 1

    scan = server_obj.get("scan") or {}
    if not (scan.get("tools") or []):
        reason = (scan.get("error") or {}).get("code") or "no tools"
        print(f"skip: not reporting ({reason}, tools=0) [{ctx}]", file=sys.stderr)
        return 0

    try:
        result = report(args.domain, args.api_key, server_obj)
    except Exception as e:
        print(f"error: report failed: {e} [{ctx}]", file=sys.stderr)
        return 1

    out = (result.stdout or "").strip()
    http_code = out.rsplit("\n", 1)[-1] if out else ""
    if http_code.startswith("2"):
        return 0
    print(f"error: report failed (http {http_code}) [{ctx}]: {out[:300]} {(result.stderr or '')[:200]}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
