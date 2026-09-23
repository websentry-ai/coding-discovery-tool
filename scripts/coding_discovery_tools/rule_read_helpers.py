"""Symlink-safe, containment-checked reading of workspace rule files.

Shared by the GitHub Copilot rules extractors (macOS/Windows/Linux). The .github
walk followed redirects, so under a root/MDM scan a user who controls their own
project could point ``.github/copilot-instructions.md`` (or the ``.github`` /
``instructions`` / ``prompts`` directory) at another user's file and have the walk
read the target as their project rule. This applies the same boundary the
Copilot-for-Xcode settings reader uses:

  * realpath containment — the file's realpath must stay inside the project root,
    so a link (symlink, or a Windows junction on an ancestor) whose target escapes
    the repo, or points at another user's file, is refused. This is the cross-OS
    guard, and the ONLY one that helps on Windows, where ``O_NOFOLLOW`` is a no-op
    and every file's ``st_uid`` is 0.
  * an ``O_NOFOLLOW`` descriptor check (POSIX) — a symlinked rule file is refused
    outright, plus non-regular files, multiply-linked files (cross-user hard link),
    and files not owned by the project owner.

So a symlinked rule file is refused (not read); only a real regular file inside the
project root is read. The walks separately refuse to descend a symlinked or
junctioned directory (``is_symlink_or_junction``) before reaching this reader.
"""

import logging
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)


def realpath_contained(path, root) -> bool:
    """True when ``path`` resolves inside ``root``. Realpath containment (not
    ``path_in_scope``, which rejects every dot-component and so could not accept a
    ``.github`` path): resolves symlinks and Windows junctions, so a path whose
    target escapes ``root`` — even through a junctioned ancestor — is refused. The
    final rule file is additionally opened O_NOFOLLOW below, so a symlinked file is
    refused rather than read."""
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
    file's realpath must stay inside ``containment_root`` and it must be a regular
    file owned by that root's owner — so a root/MDM scan cannot be tricked into
    reading another user's secret. Never raises.

    ``allow_symlink=False`` (project/workspace rules): strict. Opened O_NOFOLLOW, so
    a symlinked rule file is refused rather than followed, and a multiply-linked
    (cross-user hard link) file is refused. This is the root-scan attack surface.

    ``allow_symlink=True`` (the user's own global rules — ``~/.copilot/instructions``,
    ``~/.claude/rules``, the VS Code User prompts dir): the link is FOLLOWED so a
    dotfile manager (chezmoi/stow/yadm) that symlinks these into place still works,
    but the followed target must still resolve inside ``containment_root`` (the
    user's home) and be owned by the home's owner — so a link out of the home, or to
    another user's file, is still refused.
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
