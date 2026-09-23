"""Symlink-safe, containment-checked reading of workspace rule files.

Shared by the GitHub Copilot rules extractors. The .github walk follows redirects,
so a root/MDM scan must not read a rule file whose target escapes its containment
root (another user's file). Two scopes:

  * Project/workspace reads are STRICT — ``O_NOFOLLOW`` (a symlinked rule file is
    refused, not followed), single hard link, regular file, owned by the project.
  * User-scope global reads (the user's own ``~/.copilot/instructions`` /
    ``~/.claude/rules`` / VS Code User prompts) MAY follow a symlink (so a dotfile
    manager works), but the followed target must resolve inside the user's home and
    (macOS/Linux) be owned by that user.

realpath containment is the cross-OS guard, and the only one on Windows, where
``O_NOFOLLOW`` is a no-op and ``st_uid`` is 0. The walks separately refuse to
descend a symlinked/junctioned directory before reaching this reader.
"""

import logging
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)

# JSON/JSONC config files (opencode.json, pi models.json, Zed settings.json) can
# carry provider API keys or MCP header tokens inline. String values of keys that
# look like credentials are replaced before the content leaves the machine.
# Only string values match, so `"max_tokens": 4096` is untouched.
_SECRET_KEY_VALUE_RE = re.compile(
    r'("[^"\n]*(?:api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|'
    r'authorization|bearer|credential)[^"\n]*"\s*:\s*")((?:[^"\\\n]|\\.)*)(")',
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"
_REDACT_SUFFIXES = frozenset({".json", ".jsonc"})


def redact_secret_values(text: str) -> str:
    """Replace string values of credential-looking JSON keys with a marker."""
    return _SECRET_KEY_VALUE_RE.sub(lambda m: m.group(1) + _REDACTED + m.group(3), text)


def extract_rule_file_contained(
    rule_file: Path,
    find_project_root_func: Callable[[Path], Optional[Path]],
    scope: str = "project",
    user_home: Optional[Path] = None,
) -> Optional[Dict]:
    """``extract_single_rule_file`` shape, read through ``read_rule_file_contained``.

    Project-scope files are read strictly (no symlink, single link, owned by the
    project root). ``scope="user"`` files (the user's own global config) may be a
    symlink from a dotfile manager, but must resolve inside ``user_home`` and be
    owned by that user. ``.json``/``.jsonc`` content passes through
    ``redact_secret_values``. Returns None when the read is refused.
    """
    try:
        project_root = find_project_root_func(rule_file)
        if scope == "user" and user_home is not None:
            contained = read_rule_file_contained(rule_file, user_home, allow_symlink=True)
        else:
            contained = read_rule_file_contained(rule_file, project_root, allow_symlink=False)
        if contained is None:
            return None
        content, truncated, size, last_modified = contained
        if rule_file.suffix.lower() in _REDACT_SUFFIXES:
            content = redact_secret_values(content)
        return {
            "file_path": str(rule_file),
            "file_name": rule_file.name,
            "project_root": str(project_root) if project_root else None,
            "content": content,
            "size": size,
            "last_modified": last_modified,
            "truncated": truncated,
            "scope": scope,
        }
    except Exception as e:
        logger.warning(f"Error reading rule file {rule_file}: {e}")
        return None


def realpath_contained(path, root) -> bool:
    """True when ``path`` resolves inside ``root``. Resolves symlinks and Windows
    junctions, so a target escaping ``root`` (even through a junctioned ancestor) is
    refused. Not ``path_in_scope``, which rejects every dot-component and so could
    not accept a ``.github`` path."""
    try:
        real = os.path.realpath(str(path))
        base = os.path.realpath(str(root))
    except OSError:
        return False
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def read_rule_file_contained(
    rule_file: Path, containment_root, *, allow_symlink: bool = False
) -> Optional[Tuple[str, bool, int, str]]:
    """Read a rule file's text through the safe boundary, or None if refused.

    Returns ``(content, truncated, size, last_modified_iso)``. In both modes the
    file's realpath must stay inside ``containment_root`` and be a regular file owned
    by that root's owner. ``allow_symlink=False`` (project/workspace) also opens
    ``O_NOFOLLOW`` and refuses a multiply-linked file; ``allow_symlink=True`` (the
    user's own global rules) follows the link so a dotfile manager works, still
    contained to the home. Never raises.
    """
    if containment_root is None or not realpath_contained(rule_file, containment_root):
        logger.info(f"Refusing rule file {rule_file}: resolves outside {containment_root}")
        return None
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        if not allow_symlink:
            # O_NOFOLLOW: a symlinked final component raises here instead of being
            # followed; the descriptor, not a re-resolved path, is what we judge.
            flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(rule_file), flags)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        # Strict mode: a hard link keeps its target's owner while its path sits in
        # the repo, so containment alone cannot see through one. (User-global mode
        # follows the link, so the owner check below judges the followed target.)
        if not allow_symlink and st.st_nlink > 1:
            logger.info(f"Refusing rule file {rule_file}: multiply-linked (nlink={st.st_nlink})")
            return None
        try:
            owner = os.stat(str(containment_root)).st_uid
        except OSError:
            return None
        # A file owned by a different uid is not this owner's, even in-tree.
        if st.st_uid != owner:
            logger.info(f"Refusing rule file {rule_file}: owned by uid {st.st_uid}, root owner differs")
            return None
        size = st.st_size
        truncated = size > MAX_CONFIG_FILE_SIZE
        last_modified = datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z"
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_CONFIG_FILE_SIZE) if truncated else handle.read()
        return data.decode("utf-8", errors="replace"), truncated, size, last_modified
    except OSError as e:
        logger.debug(f"Could not read rule file {rule_file}: {e}")
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
