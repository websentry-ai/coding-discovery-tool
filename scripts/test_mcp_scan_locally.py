"""
Test driver for the integrated MCP scanning in mcp_extraction_helpers.

Sets UNBOUND_MCP_SCAN=1, loads every {name: cfg} mcpServers mapping we can
find on the machine, and passes each one through transform_mcp_servers_to_array.
Prints what each extractor would emit (with the new `scan` field attached when
scanning is on).

Run:
    python3 scripts/test_mcp_scan_locally.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from coding_discovery_tools.mcp_extraction_helpers import (  # noqa: E402
    transform_mcp_servers_to_array,
    _get_claude_oauth_index,
)


HOME = Path.home()


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _read_codex_mcp_servers(path: Path) -> Dict[str, Any]:
    """Parse Codex's TOML config and return {name: cfg} for MCP servers.

    Uses the production parser at
    coding_discovery_tools.macos.codex.mcp_config_extractor.parse_toml_mcp_servers
    so this driver exercises the same code path as a real discovery run.

    Codex sub-tables like `[mcp_servers.context7.http_headers]` are flattened
    by that parser into a fake server named `context7.http_headers`; we
    re-fold those into their parent here, mapping `http_headers` → `headers`
    so the scanner reads them correctly."""
    try:
        from coding_discovery_tools.macos.codex.mcp_config_extractor import parse_toml_mcp_servers
    except Exception:
        return {}
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    raw = parse_toml_mcp_servers(content) or {}

    parents: Dict[str, Dict[str, Any]] = {}
    sub_tables: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        if '.' in name:
            parent, sub = name.split('.', 1)
            sub_tables.setdefault(parent, {})[sub] = cfg
        else:
            parents[name] = dict(cfg)

    for parent, subs in sub_tables.items():
        if parent not in parents:
            continue
        for sub_name, sub_cfg in subs.items():
            if sub_name == 'http_headers':
                parents[parent]['headers'] = sub_cfg
            else:
                # `env` and any other sub-tables map straight through
                parents[parent][sub_name] = sub_cfg
    return parents


def gather_mappings() -> List[Tuple[str, Dict[str, Any]]]:
    """Return [(source_label, {name: cfg}), ...] for every mcpServers mapping
    we can locate on the local disk. One tuple per source file/scope."""
    out: List[Tuple[str, Dict[str, Any]]] = []

    # Claude Code global
    claude = _load_json(HOME / ".claude.json") or {}
    if claude.get("mcpServers"):
        out.append(("claude:global", claude["mcpServers"]))

    # Claude Code per-project
    for project_path, proj_cfg in (claude.get("projects") or {}).items():
        if isinstance(proj_cfg, dict) and proj_cfg.get("mcpServers"):
            out.append((f"claude:{project_path}", proj_cfg["mcpServers"]))
        # Project-local .mcp.json
        pf = Path(project_path) / ".mcp.json"
        data = _load_json(pf)
        if data and data.get("mcpServers"):
            out.append((f".mcp.json:{project_path}", data["mcpServers"]))

    # Cursor global
    cursor = _load_json(HOME / ".cursor" / "mcp.json") or {}
    if cursor.get("mcpServers"):
        out.append(("cursor:global", cursor["mcpServers"]))

    # Cursor per-project (hinted by Claude's project paths)
    for project_path in (claude.get("projects") or {}).keys():
        pf = Path(project_path) / ".cursor" / "mcp.json"
        data = _load_json(pf)
        if data and data.get("mcpServers"):
            out.append((f"cursor:{project_path}", data["mcpServers"]))

    # Codex global (~/.codex/config.toml — TOML, not JSON)
    codex_servers = _read_codex_mcp_servers(HOME / ".codex" / "config.toml")
    if codex_servers:
        out.append(("codex:global", codex_servers))

    # Windsurf global
    windsurf = _load_json(HOME / ".codeium" / "windsurf" / "mcp_config.json") or {}
    if windsurf.get("mcpServers"):
        out.append(("windsurf:global", windsurf["mcpServers"]))

    return out


def main() -> None:
    t0 = time.monotonic()
    mappings = gather_mappings()

    oauth = _get_claude_oauth_index()
    now_ms = int(time.time() * 1000)
    print(f"Claude Code OAuth tokens loaded: {len(oauth)}")
    for url, tok in oauth.items():
        remain = (tok["expires_at_ms"] - now_ms) / 86_400_000
        fresh = "fresh" if tok["expires_at_ms"] > now_ms + 60_000 else "expired"
        print(f"  {url:50s} {fresh:7s} ({remain:+.1f} days)")
    print()

    all_servers: List[Dict[str, Any]] = []
    for source, mapping in mappings:
        print(f"=== {source}  ({len(mapping)} server(s)) ===")
        transformed = transform_mcp_servers_to_array(mapping)
        for server in transformed:
            scan = server.get("scan") or {}
            error = scan.get("error")
            count = scan.get("tool_count") or 0
            if error:
                outcome = error.get("code", "error")
            elif scan:
                outcome = "scanned"
            else:
                outcome = "not_scanned"
            line = f"  {server.get('name', '?'):30s} {outcome:18s} tools={count}"
            if error and error.get("code") in ("auth_required", "auth_expired"):
                details = error.get("details") or {}
                oauth = details.get("oauth") or {}
                issuers = [
                    a.get("issuer")
                    for a in (oauth.get("authorization_servers") or [])
                    if a.get("issuer")
                ]
                if issuers:
                    line += f"  oauth={','.join(issuers)}"
                if details.get("expired_at"):
                    line += f"  expired_at={details['expired_at']}"
            print(line)
            if error and error.get("details"):
                # Surface a couple of the most useful detail fields inline for readability
                d = error["details"]
                if d.get("http_status") is not None:
                    print(f"      http_status={d['http_status']}")
                if d.get("expired_at"):
                    print(f"      expired_at={d['expired_at']}")
                if d.get("exit_code") is not None:
                    print(f"      exit_code={d['exit_code']}")
            if scan.get("tools"):
                for t in scan["tools"][:3]:
                    desc = (t.get("description") or "").replace("\n", " ")[:70]
                    print(f"      - {t.get('name'):30s}  {desc}")
                if len(scan["tools"]) > 3:
                    print(f"      ... +{len(scan['tools']) - 3} more")
            all_servers.append({"source": source, **server})
        print()

    elapsed = time.monotonic() - t0

    def _outcome(server: Dict[str, Any]) -> str:
        scan = server.get("scan") or {}
        error = scan.get("error")
        if error:
            return error.get("code") or "error"
        if scan:
            return "scanned"
        return "not_scanned"

    by_outcome = Counter(_outcome(s) for s in all_servers)
    scanned = [s for s in all_servers if _outcome(s) == "scanned"]
    tool_total = sum(((s.get("scan") or {}).get("tool_count") or 0) for s in all_servers)

    print(f"Done in {elapsed:.1f}s.")
    print(f"Servers: {len(all_servers)}  |  scanned: {len(scanned)}  |  tools: {tool_total}")
    print(f"By outcome: {dict(by_outcome)}")

    out_path = HERE / "mcp_scan_preview.json"
    payload = {
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "servers": all_servers,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nFull output written to {out_path}")


if __name__ == "__main__":
    main()
