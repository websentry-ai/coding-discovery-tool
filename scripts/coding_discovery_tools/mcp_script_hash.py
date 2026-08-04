"""Local-script MCP fingerprint support for the discovery scan.

Ports the client hook's script logic (setup/claude-code/hooks/unbound.py) so the
report carries scriptHash (-> backend `script:<hash>`) + script_content (body for
classification). Must stay byte-for-byte equivalent with the hook so the backend's
re-hash of the body matches the gateway's scriptHash.
"""

import base64
import hashlib
import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

_SCRIPT_RUNTIMES = {
    'node', 'nodejs', 'bun', 'deno', 'python', 'python2', 'python3', 'py',
    'ruby', 'dart', 'php', 'perl', 'rscript',
}
_SCRIPT_EXT_RE = re.compile(r'\.(sh|py|js|cjs|mjs|ts|tsx|rb|php|dart)$', re.IGNORECASE)
_RUNNER_SUBTOKENS = {'run', 'tsx', 'ts-node'}
MAX_SCRIPT_BYTES = 256 * 1024


def _command_basename(command: str) -> str:
    base = re.split(r'[\\/]', (command or '').strip())[-1]
    return re.sub(r'\.(exe|cmd|bat|com)$', '', base.lower())


def _looks_like_path(value: str) -> bool:
    v = (value or '').strip().strip('"\'')
    if v.startswith(('http://', 'https://', '@', 'git+')):
        return False
    # Require a script extension so a crafted config can't read arbitrary files.
    return bool(_SCRIPT_EXT_RE.search(v))


def _candidate_script(command: Optional[str], args: Optional[List]) -> Optional[str]:
    base = _command_basename(command or '')
    if base in _SCRIPT_RUNTIMES:
        for a in (args or []):
            if not isinstance(a, str) or a.startswith('-'):
                continue
            t = a.strip().strip('"\'')
            if t in _RUNNER_SUBTOKENS:
                continue
            if _looks_like_path(t):
                return t
        return None
    if command and _SCRIPT_EXT_RE.search(base):
        return command
    return None


def _resolve_script_path(command, args, cwd) -> Optional[str]:
    cand = _candidate_script(command, args)
    if not cand:
        return None
    path = os.path.expanduser(os.path.expandvars(cand.strip().strip('"\'')))
    if '${' in path:  # unexpanded env var -> can't resolve
        return None
    if not os.path.isabs(path):
        if not cwd:
            return None
        path = os.path.join(cwd, path)
    if not os.path.isfile(path):
        return None
    return path


def _read_script_bytes(command, args, cwd) -> Optional[bytes]:
    path = _resolve_script_path(command, args, cwd)
    if not path:
        return None
    try:
        with open(path, 'rb') as f:
            return f.read(MAX_SCRIPT_BYTES)
    except OSError as exc:
        # A resolved script we can't read (permission/IO) -- log a breadcrumb so
        # this is distinguishable from "not a local script", then degrade.
        logger.debug("mcp script fingerprint: could not read %s: %s", path, exc)
        return None


def augment_script_fields(server_obj: dict, cwd=None) -> dict:
    """Attach scriptHash + script_content when server_obj runs a local script."""
    if not isinstance(server_obj, dict):
        return server_obj
    command = server_obj.get('command')
    if not command:
        return server_obj
    # One read: hash and body must describe the same bytes (a mid-scan edit would
    # otherwise leave scriptHash and script_content disagreeing). Fall back to the
    # server's configured cwd so a relative script arg still resolves.
    data = _read_script_bytes(command, server_obj.get('args'), cwd or server_obj.get('cwd'))
    if data is None:
        return server_obj
    server_obj['scriptHash'] = hashlib.sha256(data).hexdigest()
    body = base64.b64encode(data).decode('ascii')
    if body:
        server_obj['script_content'] = body
    return server_obj
