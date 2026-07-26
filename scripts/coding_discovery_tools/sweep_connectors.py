#!/usr/bin/env python3
"""Resolve bare Claude connector UUIDs and report the real name + tools.

Claude desktop OAuth remote connectors (used by Claude Code and CoWork) are
named by a per-registration UUID at runtime. When a tool call only carries that
UUID, the control plane stores a metadata row named by the UUID with no
fingerprint. The real display name only exists in the local Claude session
files on this device, so the backend cannot resolve it on its own.

This sweep:
  1. asks the control plane which UUIDs still need resolving (opaque list),
  2. reads the local session files (both Claude Code and CoWork folders),
  3. for each UUID we can resolve locally, POSTs {real name, tools, connector_uuid}
     to the single-server scan endpoint, which computes the
     `claude-connector:<name>` fingerprint, applies the tools, and folds the
     UUID-named row into that keeper.

Only UUIDs the backend explicitly asked for are sent; nothing else from the
session files leaves the device. HTTP uses curl per the Zscaler constraint.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_PATH = "/api/v1/ai-tools/mcp-server-scan/"
LIST_PATH = "/api/v1/ai-tools/unresolved-connector-uuids/"

# Both folders that hold `remoteMcpServersConfig`: Claude Code and CoWork
# (local-agent-mode). Same shape, different origin.
SESSION_SUBDIRS = ("claude-code-sessions", "local-agent-mode-sessions")


def _normalize_url(url):
    return (url or "").rstrip("/")


def _curl_config_quote(value):
    """Escape a value for a curl --config double-quoted field."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _claude_base_dir():
    """The Claude application-support directory for this OS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" if appdata else None
    return Path.home() / ".config" / "Claude"


def read_local_connectors():
    """Return {uuid: {"name", "tools"}} from both session folders.

    Files are read newest-first so the current display name wins over a stale
    one. A UUID identifies one connector, so when it recurs we only enrich its
    tools from entries that carry the SAME name — a conflicting (older) name is
    ignored rather than mixing one connector's identity with another's tools.

    The session `url` is intentionally not collected: it is not needed to resolve
    the connector (the name yields the claude-connector fingerprint) and need not
    leave the device.
    """
    base = _claude_base_dir()
    out = {}
    if not base:
        return out

    files = []
    for sub in SESSION_SUBDIRS:
        folder = base / sub
        if not folder.exists():
            continue
        try:
            files.extend(folder.glob("**/local_*.json"))
        except OSError:
            continue
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in (data.get("remoteMcpServersConfig") or []):
            if not isinstance(entry, dict):
                continue
            uuid = (entry.get("uuid") or "").strip().lower()
            name = entry.get("name")
            if not uuid or not name:
                continue
            tools = entry.get("tools") if isinstance(entry.get("tools"), list) else []
            existing = out.get(uuid)
            if existing is None:
                out[uuid] = {"name": name, "tools": list(tools)}
            elif existing["name"] == name:
                existing["tools"] = _union_tools(existing["tools"], tools)
            # else: older/conflicting name for this UUID -> newest already won.
    return out


def _union_tools(a, b):
    """Union two tool lists by tool name (first occurrence wins)."""
    by_name = {}
    for t in list(a) + list(b):
        if isinstance(t, dict) and t.get("name") and t["name"] not in by_name:
            by_name[t["name"]] = t
    return list(by_name.values())


def _run_curl(args, curl_config, timeout):
    """Run curl (config fed on stdin) and split the `-w "\\n%{http_code}"`
    trailer off the body. Returns (http_code, body).

    Raises RuntimeError when curl itself fails (DNS/TLS/proxy/timeout) so the
    real reason surfaces instead of an empty status or a bare `000`.
    """
    result = subprocess.run(
        args, input=curl_config, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"curl exit {result.returncode}: {stderr[:200]}")
    out = (result.stdout or "").strip()
    if not out:
        return "", ""
    http_code = out.rsplit("\n", 1)[-1]
    body = out.rsplit("\n", 1)[0] if "\n" in out else ""
    return http_code, body


def _auth_header(api_key):
    return (
        f'header = "Authorization: Bearer {_curl_config_quote(api_key)}"\n'
        'header = "User-Agent: AI-Tools-Discovery/1.0"\n'
    )


def fetch_unresolved_uuids(domain, api_key):
    """GET the opaque list of UUIDs the backend still needs resolved."""
    url = f"{_normalize_url(domain)}{LIST_PATH}"
    http_code, body = _run_curl(
        ["curl", "-s", "--config", "-", "--max-time", "30", "-w", "\n%{http_code}", url],
        _auth_header(api_key), timeout=45,
    )
    if not http_code.startswith("2"):
        raise RuntimeError(f"list endpoint http {http_code}: {body[:200]}")
    parsed = json.loads(body) if body else {}
    return [u for u in (parsed.get("uuids") or []) if u]


def report_connector(domain, api_key, connector_uuid, name, tools):
    """POST one resolved connector to the single-server scan endpoint.
    Returns (http_code, body)."""
    endpoint = f"{_normalize_url(domain)}{REPORT_PATH}"
    mcp_server = {
        "name": name,
        "additional_data": {"scope": "claude-connector"},
        "scan": {
            "tools": tools or [],
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    payload = json.dumps({"mcp_server": mcp_server, "connector_uuid": connector_uuid})
    curl_config = (
        _auth_header(api_key)
        + 'header = "Content-Type: application/json"\n'
        + f'data = "{_curl_config_quote(payload)}"\n'
    )
    return _run_curl(
        ["curl", "-s", "-X", "POST", "--config", "-", "--max-time", "60", "-w", "\n%{http_code}", endpoint],
        curl_config, timeout=90,
    )


def run_sweep(domain, api_key):
    """Fetch the backend's unresolved-UUID worklist, match it against the local
    Claude session connectors, and report each resolution. Best-effort and
    side-effect-light: logs progress to stderr and returns (sent, failed, matched).
    Designed to be called from the main discovery run as well as standalone."""
    needed = {u.strip().lower() for u in fetch_unresolved_uuids(domain, api_key) if u}
    if not needed:
        print("info: connector sweep — nothing to resolve", file=sys.stderr)
        return (0, 0, 0)

    local = read_local_connectors()
    matches = {u: local[u] for u in needed if u in local}
    print(f"info: connector sweep — {len(needed)} needed, {len(matches)} resolvable locally",
          file=sys.stderr)
    if not matches:
        return (0, 0, 0)

    sent = failed = 0
    for uuid, info in matches.items():
        try:
            http_code, body = report_connector(domain, api_key, uuid, info["name"], info.get("tools"))
        except Exception as e:
            failed += 1
            print(f"error: report failed for {uuid}: {e}", file=sys.stderr)
            continue
        if http_code.startswith("2"):
            sent += 1
            print(f"info: resolved {uuid} -> {info['name']} ({len(info.get('tools') or [])} tools)",
                  file=sys.stderr)
        else:
            failed += 1
            print(f"error: report failed for {uuid} (http {http_code}): {body[:200]}", file=sys.stderr)

    print(f"info: connector sweep — resolved {sent}, failed {failed}, of {len(matches)} matches",
          file=sys.stderr)
    return (sent, failed, len(matches))


def main():
    parser = argparse.ArgumentParser(description="Resolve bare Claude connector UUIDs.")
    parser.add_argument("--domain", required=True, help="Control-plane base URL")
    parser.add_argument("--api-key", default=os.environ.get("UNBOUND_API_KEY"),
                        help="Discovery/gateway API key (defaults to UNBOUND_API_KEY env)")
    args = parser.parse_args()

    if not args.api_key:
        print("error: no api key (pass --api-key or set UNBOUND_API_KEY)", file=sys.stderr)
        return 2

    try:
        _sent, failed, _matched = run_sweep(args.domain, args.api_key)
    except Exception as e:
        print(f"error: sweep failed: {e}", file=sys.stderr)
        return 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
