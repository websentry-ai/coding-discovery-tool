"""Symlink-safe, containment-checked reading of workspace rule files.

Shared by the GitHub Copilot rules extractors. The .github walk follows redirects,
so a root/MDM scan must not read a rule file whose target escapes its containment
root (another user's file). Two scopes:

  * Project/workspace reads are STRICT — resolved one component at a time from a
    handle on the project root with ``O_NOFOLLOW`` (``openat``), so no symlinked
    component BELOW the root, intermediate or final, is ever followed. The root
    anchor itself is opened by name and trusted (a root swapped for a symlink after
    the discovery walk is not caught).
  * User-scope global reads (the user's own ``~/.copilot/instructions`` /
    ``~/.claude/rules`` / VS Code User prompts) MAY follow a symlink (so a dotfile
    manager works), so they open the path and then contain the OPENED descriptor's
    kernel path to the user's home; the ``st_uid`` owner check is the real backstop.

Windows has no per-component ``openat`` and cannot ``os.open`` a directory, so it
opens the file and contains the handle's real path (``GetFinalPathNameByHandle``).
The walks separately refuse to descend a symlinked/junctioned directory before
reaching this reader.
"""

import logging
import os
import platform
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credential redaction for JSON/JSONC config text
#
# opencode.json[c], pi settings/models.json and Zed settings.json can carry
# provider API keys, MCP env maps, header tokens and connection strings. Before
# any of that leaves the machine, `redact_secret_values` rewrites string VALUES
# through a small JSONC tokenizer (not regexes over raw text), so comments
# between a key and its value, comments before a block, nested structure and a
# file truncated mid-string are all handled. Keys, numbers, booleans and
# structure are preserved so the config stays readable.
#
# A string value is redacted when ANY of:
#   - its key name looks like a credential (apiKey, api_key, token, secret, ...)
#   - it sits anywhere inside an `env` / `environment` / `headers` object
#   - it follows a credential-looking CLI flag in an array (["--api-key", "…"])
#   - it is an inline `--flag=value` credential flag (value part only)
#   - it is a URL with userinfo (scheme://user:pass@host -> userinfo redacted)
#   - it is a `url`/`uri`/`endpoint` value with a query string (query dropped)
#   - the string is unterminated at EOF (truncated file) -> fail closed
# Comment bodies (// and /* */) are replaced with a placeholder outright, since
# commented-out keys are a common way to park an old credential.
# ---------------------------------------------------------------------------

_REDACTED = "***REDACTED***"
_COMMENT_LINE = "// [comment redacted]"
_COMMENT_BLOCK = "/* [comment redacted] */"
_REDACT_SUFFIXES = frozenset({".json", ".jsonc"})

_CRED_KEY_RE = re.compile(
    r"api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|"
    r"authorization|bearer|credential",
    re.IGNORECASE,
)
_SECRET_BLOCK_KEYS = frozenset({"env", "environment", "headers"})
_URL_KEYS = frozenset({"url", "uri", "endpoint"})
_CRED_FLAG_RE = re.compile(
    r"^--?[A-Za-z0-9_-]*(?:key|token|secret|password|passwd|auth|credential)[A-Za-z0-9_-]*$",
    re.IGNORECASE,
)
_CRED_FLAG_INLINE_RE = re.compile(
    r"^(--?[A-Za-z0-9_-]*(?:key|token|secret|password|passwd|auth|credential)[A-Za-z0-9_-]*=)(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# scheme://userinfo@  -> the userinfo part. Requires a scheme so a bare
# "user:pass@host" prose fragment is not touched.
_URL_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/?#@\s]+@)")


class _Frame(object):
    __slots__ = ("kind", "key", "expect", "secret", "prev_flag")

    def __init__(self, kind, secret):
        self.kind = kind          # "obj" | "arr"
        self.key = None           # current key (obj)
        self.expect = "key"       # obj: "key" | "value"
        self.secret = secret      # inside an env/headers block
        self.prev_flag = False    # arr: previous element was a credential flag


def _scan_string(text, i):
    """``text[i]`` is an opening quote. Return (end_index_of_closing_quote_or_n,
    terminated)."""
    n = len(text)
    j = i + 1
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == '"':
            return j, True
        if ch == "\n":
            # JSON strings cannot span lines; treat as unterminated (truncated
            # or malformed) and fail closed.
            return j, False
        j += 1
    return n, False


def _transform_value(raw, frame):
    """The replacement for string value ``raw`` given its context."""
    if frame is not None:
        if frame.secret:
            return _REDACTED
        if frame.kind == "obj" and frame.key is not None and _CRED_KEY_RE.search(frame.key):
            return _REDACTED
        if frame.kind == "arr" and frame.prev_flag:
            return _REDACTED
    m = _CRED_FLAG_INLINE_RE.match(raw)
    if m:
        return m.group(1) + _REDACTED
    out = _URL_USERINFO_RE.sub(lambda u: u.group(1) + _REDACTED + "@", raw)
    if frame is not None and frame.kind == "obj" and frame.key is not None \
            and frame.key.lower() in _URL_KEYS and "?" in out:
        out = out.split("?", 1)[0] + "?" + _REDACTED
    return out


def redact_secret_values(text):
    """Redact credentials in JSON/JSONC config text (see module comment above).

    Never raises; on malformed input it degrades to redacting every string in
    value position it cannot classify as safe.
    """
    out = []
    stack = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # Comments are dropped to a placeholder: users park old keys in
        # `// "apiKey": "sk-…"` lines, and comment text is never structure the
        # backend needs. Line breaks are preserved so line numbers still line up.
        if ch == "/" and nxt == "/":
            nl = text.find("\n", i)
            j = n if nl == -1 else nl
            out.append(_COMMENT_LINE)
            i = j
            continue
        if ch == "/" and nxt == "*":
            close = text.find("*/", i + 2)
            j = n if close == -1 else close + 2
            out.append(_COMMENT_BLOCK)
            out.append("\n" * text.count("\n", i, j))
            i = j
            continue
        if ch == '"':
            j, terminated = _scan_string(text, i)
            raw = text[i + 1:j]
            frame = stack[-1] if stack else None
            if frame is not None and frame.kind == "obj" and frame.expect == "key":
                frame.key = raw
                out.append(text[i:j + 1] if terminated else '"' + _REDACTED)
            else:
                if not terminated:
                    value = _REDACTED
                else:
                    value = _transform_value(raw, frame)
                out.append('"' + value + ('"' if terminated else ""))
                if frame is not None and frame.kind == "arr":
                    frame.prev_flag = bool(_CRED_FLAG_RE.match(raw))
            i = j + 1 if terminated else j
            if not terminated and j < n:
                # Newline inside a string: keep scanning after it.
                out.append(text[j])
                i = j + 1
            continue
        if ch == ":":
            if stack and stack[-1].kind == "obj":
                stack[-1].expect = "value"
        elif ch == ",":
            if stack:
                top = stack[-1]
                if top.kind == "obj":
                    top.expect = "key"
                    top.key = None
        elif ch == "{" or ch == "[":
            parent = stack[-1] if stack else None
            secret = False
            if parent is not None:
                secret = parent.secret or (
                    parent.kind == "obj" and parent.key is not None
                    and parent.key.lower() in _SECRET_BLOCK_KEYS
                )
            stack.append(_Frame("obj" if ch == "{" else "arr", secret))
        elif ch == "}" or ch == "]":
            if stack:
                stack.pop()
            if stack and stack[-1].kind == "obj":
                stack[-1].expect = "key"
                stack[-1].key = None
        out.append(ch)
        i += 1
    return "".join(out)


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


def _fd_real_path(fd: int) -> Optional[str]:
    """The kernel's own path for an open fd, read from the descriptor so a later path
    swap can't change it, or None. Never re-walked with ``realpath``: Linux ``readlink``
    of the fd magic-link, macOS ``F_GETPATH``, Windows ``GetFinalPathNameByHandle``."""
    system = platform.system()
    if system == "Windows":
        from .utils import _windows_final_path, _strip_extended_prefix
        final = _windows_final_path(fd)
        return _strip_extended_prefix(final) if final else None
    if system == "Darwin":
        try:
            import fcntl
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\x00" * 1024)
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace") or None
        except (OSError, ValueError):
            return None
    try:
        return os.readlink("/proc/self/fd/%d" % fd)
    except OSError:
        return None


def _dir_real_path(root) -> Optional[str]:
    """The kernel's own path for directory ``root``, or None. POSIX takes it from an
    open directory handle so an ancestor swap can't forge it; Windows can't ``os.open``
    a directory, so it resolves the name (the file side is still handle-bound)."""
    if platform.system() == "Windows":
        from .utils import _strip_extended_prefix
        try:
            return _strip_extended_prefix(os.path.realpath(str(root)))
        except OSError:
            return None
    dir_fd = None
    try:
        dir_fd = os.open(str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        return _fd_real_path(dir_fd)
    except OSError:
        return None
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass


def _fd_within_root(fd: int, root) -> bool:
    """True when the file the fd actually opened resolves inside ``root``. Both sides
    are handle/kernel paths compared after ``normpath`` only, never re-walked by name,
    so the check holds on Windows too (where ``O_NOFOLLOW`` and ``st_uid`` are inert)."""
    real = _fd_real_path(fd)
    base = _dir_real_path(root)
    if real is None or base is None:
        return False
    real_n = os.path.normcase(os.path.normpath(real))
    base_n = os.path.normcase(os.path.normpath(base))
    return real_n == base_n or real_n.startswith(base_n.rstrip(os.sep) + os.sep)


def _open_beneath_strict(rule_file, root, final_flags) -> Optional[int]:
    """POSIX: open ``rule_file`` by descending each path component from a handle on
    ``root`` with ``O_NOFOLLOW`` (``openat``), or None. No symlinked component below
    the root is followed. The root anchor itself is opened by name and trusted (a root
    swapped for a symlink is not caught)."""
    try:
        rel = os.path.relpath(os.path.abspath(str(rule_file)), os.path.abspath(str(root)))
    except (OSError, ValueError):
        return None
    parts = [p for p in rel.split(os.sep) if p and p != os.curdir]
    if not parts or os.pardir in parts:  # empty, or escapes root with ".."
        return None
    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not o_nofollow:  # no O_NOFOLLOW: fail closed rather than open a symlink
        return None
    o_directory = getattr(os, "O_DIRECTORY", 0)
    dir_fd = None
    try:
        dir_fd = os.open(str(root), os.O_RDONLY | o_directory)  # trusted anchor
        for comp in parts[:-1]:
            next_fd = os.open(comp, os.O_RDONLY | o_directory | o_nofollow, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        return os.open(parts[-1], final_flags | o_nofollow, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass


def _open_contained(rule_file, root, allow_symlink, *, extra_flags: int = 0) -> Optional[int]:
    """The single OS-dispatched open for contained reads, or None. ``extra_flags`` are
    OR'd in so a caller can add its own (e.g. ``O_BINARY``). The follow-symlink
    pre-check is best-effort; ``_fd_within_root`` on the descriptor and the uid check
    are the authoritative guards. A symlinked containment root is trusted as-is."""
    base_flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
                  | getattr(os, "O_NOCTTY", 0) | extra_flags)
    if os.name == "posix" and not allow_symlink:
        # Project scope: resolve per component with O_NOFOLLOW; no symlink below root.
        return _open_beneath_strict(rule_file, root, base_flags)
    # Follow-symlink scope (user, or any Windows: dir_fd is unsupported there). Pre-check
    # the target before opening; a symlink could point outside root or at a device node.
    try:
        resolved = os.path.realpath(str(rule_file))
        base = os.path.realpath(str(root))
    except OSError:
        return None
    if not (resolved == base or resolved.startswith(base.rstrip(os.sep) + os.sep)):
        return None
    try:
        if not stat.S_ISREG(os.stat(resolved).st_mode):
            return None
    except OSError:
        return None
    flags = base_flags | (0 if allow_symlink else getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(str(rule_file), flags)
    except OSError:
        return None
    # Authoritative containment check, bound to the opened descriptor.
    if not _fd_within_root(fd, root):
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    return fd


def read_rule_file_contained(
    rule_file: Path, containment_root, *, allow_symlink: bool = False
) -> Optional[Tuple[str, bool, int, str]]:
    """Read a rule file's text through the safe boundary, or None if refused.

    Returns ``(content, truncated, size, last_modified_iso)``. Containment is enforced
    on the OPENED file: ``allow_symlink=False`` (project/workspace) resolves it per
    component from the root with ``O_NOFOLLOW`` and refuses a multiply-linked file;
    ``allow_symlink=True`` (the user's own global rules) follows the link so a dotfile
    manager works, then contains the resolved descriptor to the home. The file must be
    a regular file owned by the root's owner. Never raises.
    """
    if containment_root is None:
        logger.info(f"Refusing rule file {rule_file}: no containment root")
        return None
    fd = None
    try:
        fd = _open_contained(rule_file, containment_root, allow_symlink)
        if fd is None:
            logger.info(f"Refusing rule file {rule_file}: not contained under {containment_root}")
            return None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        # Strict mode: a hard link (even same-uid) passes containment and the owner check; only nlink > 1 refuses it.
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
