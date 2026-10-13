"""VS Code's MCP governance, read from the editor's own settings.

VS Code decides which MCP servers *any* agent inside it may connect to, through
three workbench settings. The decision is the editor's, registered by the
workbench rather than by an extension, so it binds every agent surface in that
window.

It is read here, and not inside one tool's permission extractor, for two
reasons: reading it there attributes an editor-wide rule to whichever agent
happens to own the extractor, and it disappears entirely when that agent is not
installed even though the restriction is still in force.

One record per settings file that defines any of the three keys. Profiles are
not merged: VS Code applies the file belonging to the active profile, so a
restrictive default and a permissive named profile are two different postures
and collapsing them would report neither.
"""
import logging
from typing import Dict, List, Optional

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

