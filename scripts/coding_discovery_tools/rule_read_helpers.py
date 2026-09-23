"""Symlink-safe, containment-checked reading of workspace rule files.

Shared by the GitHub Copilot rules extractors (macOS/Windows/Linux). The .github
walk follows symlinks, so under a root/MDM scan a user who controls their own
project could point ``.github/copilot-instructions.md`` (or the ``.github`` /
``instructions`` / ``prompts`` directory) at another user's file and have the walk
read the target as their project rule. This applies the same boundary the
Copilot-for-Xcode settings reader uses: realpath containment within the project
root plus an O_NOFOLLOW descriptor check (regular file, single hard link, owned by
the project owner), so an in-repo symlink to an in-repo file is still read but a
link pointing outside the repo — or to another user's file — is refused.
"""

import logging
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)

_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


def realpath_contained(path, root) -> bool:
    """True when ``path`` resolves inside ``root``. Realpath containment (not
    ``path_in_scope``, which rejects every dot-component and so could not accept a
    ``.github`` path): an in-repo symlink whose target stays inside the repo is
    allowed, one pointing outside is refused."""
    try:
        real = os.path.realpath(str(path))
        base = os.path.realpath(str(root))
    except OSError:
        return False
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def read_rule_file_contained(rule_file: Path, project_root) -> Optional[Tuple[str, bool, int, str]]:
    """Read a rule file's text through the safe boundary, or None if refused.

    Returns ``(content, truncated, size, last_modified_iso)``. Refuses a file whose
    realpath escapes ``project_root``, a symlinked final component (O_NOFOLLOW),
    a non-regular file, a multiply-linked file, or one not owned by the project
    owner — so a root/MDM scan cannot be tricked into reading another user's secret.
    Never raises.
    """
    if project_root is None or not realpath_contained(rule_file, project_root):
        logger.info(f"Refusing rule file {rule_file}: resolves outside its project root")
        return None
    fd = None
    try:
        # O_NOFOLLOW: a symlinked final component raises here instead of being
        # followed; the descriptor, not a re-resolved path, is what we judge.
        fd = os.open(str(rule_file), _OPEN_FLAGS)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        # A hard link keeps its target's owner while its path sits in the repo, so
        # containment alone cannot see through one; st_nlink catches it.
        if st.st_nlink > 1:
            logger.info(f"Refusing rule file {rule_file}: multiply-linked (nlink={st.st_nlink})")
            return None
        try:
            owner = os.stat(str(project_root)).st_uid
        except OSError:
            return None
        # A file owned by a different uid is not this project owner's, even in-tree.
        if st.st_uid != owner:
            logger.info(f"Refusing rule file {rule_file}: owned by uid {st.st_uid}, project owner differs")
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
