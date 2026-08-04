# MCP script fingerprinting in discovery (deferred)

Status: **not started**. Deferred until after the plugin MCP registry-first port.
This document exists so the context survives the gap.

## The gap

The Claude Code hook computes two things for a stdio MCP server whose command
runs a local script, and attaches them to the server config it reports:

- `scriptHash` — sha256 of the script body
- `script_content` — base64 of the same body

The discovery tool computes neither. It reports the same server with only
`command` / `args` / `url`.

## Why it matters

**Identity.** The backend derives an MCP server's fingerprint from the config.
For a local script there is nothing stable to key on — `python3 ./server.py`
says nothing about which server it is. `compute_fingerprint` therefore prefers a
`script:<hash>` identity when a hash is supplied. A server reported by the hook
gets `script:<hash>`; the same server reported by the discovery sweep falls back
to command/args heuristics and lands on a **different metadata row**. Split
analytics, duplicated canonicalisation work, and policies attached to one
identity that don't apply to the other.

**Canonicalisation.** The LLM that assigns a canonical group has no idea what a
local-script server actually is without seeing the script. Command and args
alone (`node /Users/x/tools/mcp/index.js`) carry no signal.

Note the current state honestly: the body is **stored** on the single-scan path
but is **not** in the classification prompt today. See "Backend state" below —
closing the canonicalisation goal needs two backend changes, not one.

## What to port (source of truth)

All in `setup/claude-code/hooks/unbound.py`:

| Symbol | Line | Purpose |
|---|---|---|
| `_HOOK_SCRIPT_RUNTIMES` | 1009 | interpreters whose script arg is the real target |
| `_HOOK_SCRIPT_EXT_RE` | 1013 | recognised script extensions |
| `_HOOK_RUNNER_SUBTOKENS` | 1014 | `run` / `tsx` / `ts-node` — skip, not the script |
| `_hook_command_basename` | 1017 | strip path and `.exe`/`.cmd`/`.bat` |
| `_hook_looks_like_path` | 1022 | is this arg a local script path? |
| `_hook_candidate_script` | 1032 | the one local script this config runs, or None |
| `_compute_script_hash` | 1051 | sha256 over the first 256 KB |
| `_read_script_body_b64` | 1143 | base64 of the **same** 256 KB prefix |
| `_augment_script_hash` | 1130 | attaches `scriptHash` to a resolved server config |
| `_HOOK_MAX_SCRIPT_BYTES` | 1140 | 256 KB cap |

Rules that must survive the port — each exists for a reason:

- Only a **recognised script extension** counts (`.sh .py .js .cjs .mjs .ts .tsx
  .rb .php .dart`). An earlier version treated any `/`-containing arg as a
  script, which let a crafted config (`python3 /etc/passwd`) get an arbitrary
  file read and uploaded.
- Skip `http://`, `https://`, `@scoped/pkg`, `git+…` — those are packages, not
  local files.
- Skip runner subtokens (`bun run x`, `npx tsx y`).
- `expanduser` + `expandvars`, then bail if `${` survives — an unexpandable env
  var means we cannot resolve the path, and guessing is worse than skipping.
- Relative paths resolve against a base dir.
- The hash and the body must read the **same capped prefix**. The backend
  re-hashes the bytes it receives; if the body is truncated differently from
  what was hashed, every fingerprint mismatches.

## What is genuinely new (not a port)

The hook gets `cwd` from the hook event. Discovery has no event, so the base
directory for resolving a relative script path has to come from where the config
file was found:

| Config source | Base dir |
| --- | --- |
| plugin `.mcp.json` / `plugin.json` | plugin (or version) dir |
| project-scope `.mcp.json` | project root |
| user-scope config | the config file's directory |

`transform_mcp_servers_to_array` (`mcp_extraction_helpers.py:616`) currently
takes only the server mapping. It needs an optional `base_dir` kwarg defaulting
to `None`, where `None` means "no fingerprinting" so omitting it is a no-op.
Roughly 15 call sites across `mcp_extraction_helpers.py` and the per-tool
extractors.

Decided: this applies to **all five tools** (Claude Code, Cursor, Codex,
Copilot, Augment), since the transform is shared.

## Backend state (verified in `ai-gateway-data`)

**`scriptHash` on the bulk discovery path already works.** `_process_mcp_server`
(`webapp/services/ai_tools_service.py:6773`) reads it, excludes it from
`additional_data` (`:6760`) so it can't pollute the identity, and passes it to
`compute_fingerprint`. No backend change needed — the field is plumbed and
simply never populated, because discovery is the only sender on that path.

**`script_content` on the bulk path is accepted and dropped.** It is excluded
from the unknown-field check so it won't fail validation, but nothing persists
it. The path deliberately writes no `source_config` (`:6810`) because it creates
an `MCPServer` row and the classifier reads config through that join.

`_persist_source_config` (`:6429`) is single-scan only. Worth reading before
touching it — it merges rather than overwrites (so a config-only rescan cannot
wipe a previously stored body) and takes a row lock (concurrent scans otherwise
read-modify-write the same JSONB and drop each other's keys).

**The classification prompt does not read `script_content`.**
`mcp_canonical_group_classification_task.py:462-490` builds from
`source_config`'s `command`, `args` and `additional_data` only.

So the canonicalisation goal needs two backend changes:

1. Persist `script_content` from the bulk path — either call
   `_persist_source_config` there, or add an equivalent that doesn't conflict
   with the MCPServer-row assumption that path is built on.
2. Include the body in the classification prompt, capped and treated as
   untrusted the way `_cap_untrusted` already treats `additional_data`.

## Open questions

- **Payload size.** 256 KB of base64 is ~350 KB per server, and a bulk report
  can carry many. Options: cap per report, send the body only when the
  fingerprint would otherwise be null, or send it out-of-band on the existing
  single-server endpoint. Needs a decision before implementing.
- **Cadence.** Send the body on every sweep, or only on first sight and when the
  hash changes?
- **Prompt injection.** Script bodies are attacker-controllable text heading
  into an LLM prompt. `_cap_untrusted` bounds length, not instructions.
- Is 256 KB the right cap for discovery, given it is not on a latency-critical
  path the way the hook is?

## Decisions already made

- All five tools, via the shared transform.
- `base_dir` as an optional kwarg, default `None`.
- Same capped prefix for both hash and body.
- If this ships in pieces: `scriptHash` first (works end to end today with zero
  backend work), `script_content` once the backend can store **and** prompt it.
