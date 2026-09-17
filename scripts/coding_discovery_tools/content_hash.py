"""
Per-tool content hash for MCP tool risk scoring.

⚠️ KEEP IN SYNC: this logic is duplicated in the Django control plane at
`ai-gateway-data/webapp/services/mcp_content_hash.py`. The hook relays hashes
computed here and Django looks scores up by hashes it computed itself, so the
two implementations MUST produce byte-identical output. Any change to the
field subset or the JSON encoding here requires the same change there (and
vice-versa). Reviewers / the code-review bot: flag PRs that touch one without
the other.

The hash covers the fields that determine a tool's risk: description,
inputSchema, annotations. `name` is deliberately excluded (it is the key
stored next to the hash), `title` is cosmetic, and `outputSchema` is excluded
per the risk-design doc. Input is the trimmed tool object produced by
`mcp_extraction_helpers._trim_tools` (name/title/description/inputSchema/
outputSchema/annotations).
"""
import hashlib
import json

_CONTENT_HASH_FIELDS = ("description", "inputSchema", "annotations")


def compute_tool_content_hash(tool: dict) -> str:
    """sha256 over the canonical JSON encoding of the risk-relevant tool fields.

    Keys that are absent or null are omitted from the hashed subset.
    `ensure_ascii` is left at its default (True) — both implementations are
    Python; keep them byte-identical.
    """
    subset = {k: tool[k] for k in _CONTENT_HASH_FIELDS if tool.get(k) is not None}
    encoded = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
