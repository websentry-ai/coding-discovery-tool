"""VS Code's MCP governance, read from the editor's own settings.

VS Code decides which MCP servers *any* agent inside it may connect to, through
three workbench settings. The decision is the editor's, registered by the
workbench rather than by an extension, so it binds every agent surface in that
window.

It is read here, and not inside one tool's permission extractor, for two
reasons: reading it there attributes an editor-wide rule to whichever agent
happens to own the extractor, and it disappears entirely when that agent is not
installed even though the restriction is still in force.

One record per settings file that declares any of the three keys, carrying that
file's path. Profiles stay separate because VS Code applies only the active
profile's file: a restrictive default and a permissive named profile are two
different postures, and a union of them is a policy neither file declares.

Per user, too. The path on each record is what the per-user report filter matches
against a home directory, so an elevated scan credits each account with its own
decision instead of the first one found.
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .mcp_extraction_helpers import (
    _strip_jsonc_comments,
    _strip_trailing_commas,
    enumerate_vscode_user_files,
)

logger = logging.getLogger(__name__)

ACCESS_KEY = "chat.mcp.access"
ALLOWED_KEY = "chat.mcp.allowedServers"
DENIED_KEY = "chat.mcp.deniedServers"
GOVERNANCE_KEYS = (ACCESS_KEY, ALLOWED_KEY, DENIED_KEY)

# The workbench accepts these three; anything else is reported verbatim as
# unknown rather than coerced, since a typo'd value is itself worth seeing.
KNOWN_ACCESS_VALUES = ("all", "registry", "none")


def _server_names(value) -> List[str]:
    """The server identifiers in an allow/deny setting.

    VS Code accepts either a list of names or an object keyed by name, and the
    object form is what the settings UI writes. Both reduce to the names; the
    values carry no identity.
    """
    if isinstance(value, dict):
        return [str(k) for k in value.keys()]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def governance_from_settings(data: Dict) -> Optional[Dict]:
    """The governance one settings mapping declares, or None when it declares none.

    Absence is not permission: a file with none of these keys says nothing about
    MCP access, so it yields no record rather than a record meaning "all".
    """
    if not isinstance(data, dict):
        return None
    present = [k for k in GOVERNANCE_KEYS if k in data]
    if not present:
        return None

    access = data.get(ACCESS_KEY)
    record = {
        "scope": "vscode",
        "allowed_servers": _server_names(data.get(ALLOWED_KEY)),
        "denied_servers": _server_names(data.get(DENIED_KEY)),
    }
    if ACCESS_KEY in data:
        record["access"] = access if isinstance(access, str) else None
        record["access_is_known"] = (
            isinstance(access, str) and access in KNOWN_ACCESS_VALUES)
    return record



def governance_for_user(config_dirs, user_home, read_text) -> List[Dict]:
    """One record per settings file under these VS Code ``User`` dirs.

    ``read_text(path, user_home)`` is the caller's hardened reader -- the one
    owning the uid and file-swap checks. An elevated scan must not reach a
    user's file through a weaker path just because this signal is editor-scoped.

    Never raises: a file that cannot be read or parsed is skipped, because one
    unreadable profile must not cost the others.
    """
    import json

    records: List[Dict] = []
    for config_dir in config_dirs or []:
        for path in enumerate_vscode_user_files(Path(config_dir), "settings.json"):
            try:
                raw = read_text(path, user_home)
                if not raw:
                    continue
                data = json.loads(_strip_trailing_commas(_strip_jsonc_comments(raw)))
                record = governance_from_settings(data)
                if not record:
                    continue
                record["settings_source"] = "vscode_mcp_governance"
                record["settings_path"] = str(path)
                record["raw_settings"] = {
                    k: v for k, v in data.items() if k in GOVERNANCE_KEYS
                }
                records.append(record)
            except Exception as exc:
                logger.debug("vscode mcp governance: skipping %s: %s", path, exc)
    return records
