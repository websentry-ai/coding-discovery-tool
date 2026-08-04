"""Local-script MCP fingerprint support for the discovery scan.

Ports the client hook's script logic (setup/claude-code/hooks/unbound.py) so the
report carries scriptHash (-> backend `script:<hash>`) + script_content (body for
classification). Must stay byte-for-byte equivalent with the hook so the backend's
re-hash of the body matches the gateway's scriptHash.
"""

import base64
import hashlib
import os
import re
from typing import List, Optional

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


def compute_script_hash(command, args, cwd=None) -> Optional[str]:
    try:
        path = _resolve_script_path(command, args, cwd)
        if not path:
            return None
        h = hashlib.sha256()
        remaining = MAX_SCRIPT_BYTES
        with open(path, 'rb') as f:
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()
    except Exception:
        return None


def read_script_body_b64(command, args, cwd=None) -> Optional[str]:
    try:
        path = _resolve_script_path(command, args, cwd)
        if not path:
            return None
        with open(path, 'rb') as f:
            data = f.read(MAX_SCRIPT_BYTES)
        return base64.b64encode(data).decode('ascii')
    except Exception:
        return None


def augment_script_fields(server_obj: dict, cwd=None) -> dict:
    """Attach scriptHash + script_content when server_obj runs a local script."""
    if not isinstance(server_obj, dict):
        return server_obj
    command = server_obj.get('command')
    if not command:
        return server_obj
    args = server_obj.get('args')
    script_hash = compute_script_hash(command, args, cwd)
    if not script_hash:
        return server_obj
    server_obj['scriptHash'] = script_hash
    body = read_script_body_b64(command, args, cwd)
    if body:
        server_obj['script_content'] = body
    return server_obj
