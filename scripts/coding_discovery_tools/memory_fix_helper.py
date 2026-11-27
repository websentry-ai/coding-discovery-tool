"""Removes confirmed credentials from one memory file; runs as the file's owner with no key, network or config."""
import errno
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
from typing import Dict, List, Optional, Tuple

PROTOCOL = 1
MARKER = "[credential removed by Unbound]"
MAX_FILE_BYTES = 50 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
FAILURE_REASONS = ("changed", "missing", "too_large", "not_utf8", "refused")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_RENAME_SWAP = 0x2  # macOS renameatx_np: exchange the two names in one step
_COPYFILE_ACL_XATTR = (1 << 0) | (1 << 2)  # macOS fcopyfile: COPYFILE_ACL | COPYFILE_XATTR
_SWAP_BACK_TRIES = 4
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)


class _Failed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _os_failure(error: OSError) -> _Failed:
    return _Failed("missing" if error.errno == errno.ENOENT else "refused")


def _close(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _contained_parts(file_path: str, memory_root: str) -> List[str]:
    """The components of file_path from /, refused unless it sits below memory_root once `..` is resolved by text."""
    if not posixpath.isabs(file_path) or not posixpath.isabs(memory_root):
        raise _Failed("refused")
    path, root = posixpath.normpath(file_path), posixpath.normpath(memory_root)
    if not path.startswith(root.rstrip("/") + "/"):
        raise _Failed("refused")
    return [part for part in path.split("/") if part]


def _open_parent(parts: List[str]) -> int:
    """A handle on the file's folder, reached from / one component at a time without following any link."""
    if not getattr(os, "O_NOFOLLOW", 0):
        raise _Failed("refused")
    try:
        dir_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as error:
        raise _Failed("refused") from error
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        return dir_fd
    except OSError as error:
        _close(dir_fd)
        raise _os_failure(error) from error


def _owned_single_file(file_stat: os.stat_result) -> bool:
    return (stat.S_ISREG(file_stat.st_mode) and file_stat.st_nlink == 1
            and file_stat.st_uid == os.geteuid())


def _read_file(dir_fd: int, name: str) -> Tuple[bytes, os.stat_result]:
    """The file's bytes and its stat, refused for a link, a hard link, another owner or a non-regular file."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except OSError as error:
        raise _os_failure(error) from error
    try:
        file_stat = os.fstat(fd)
        if not _owned_single_file(file_stat):
            raise _Failed("refused")
        if file_stat.st_size > MAX_FILE_BYTES:
            raise _Failed("too_large")
        chunks, size = [], 0
        while size <= MAX_FILE_BYTES:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise _Failed("too_large")
        return b"".join(chunks), file_stat
    except OSError as error:
        raise _Failed("refused") from error
    finally:
        _close(fd)


def _valid_spans(spans: List, length: int) -> bool:
    """Spans are [start, end] int pairs, sorted, not overlapping and inside the text."""
    previous_end = 0
    for span in spans:
        if not (isinstance(span, list) and len(span) == 2
                and all(isinstance(edge, int) and not isinstance(edge, bool) for edge in span)):
            return False
        start, end = span
        if not previous_end <= start < end <= length:
            return False
        previous_end = end
    return bool(spans)


def _replaced(text: str, spans: List) -> str:
    """text with each span replaced by the marker, from the end backwards so earlier cuts never shift later ones."""
    for start, end in reversed(spans):
        text = text[:start] + MARKER + text[end:]
    return text


def _still_same(dir_fd: int, name: str, before: os.stat_result) -> bool:
    """True when the name still holds the very file that was read, unchanged."""
    try:
        now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return (_owned_single_file(now) and (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns)
            == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))


def _remove(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _copy_access(dir_fd: int, name: str, to_fd: int, group: int) -> None:
    """Carries the original's group, ACL and extended attributes onto the new file where the OS can; best effort."""
    from_fd = None
    try:
        os.fchown(to_fd, -1, group)
    except OSError:
        pass  # Not a group this user may give the file; it keeps the folder's.
    try:
        import ctypes
        copy = ctypes.CDLL(None, use_errno=True).fcopyfile
        copy.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        from_fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
        copy(from_fd, to_fd, None, _COPYFILE_ACL_XATTR)
    except (ImportError, OSError, AttributeError, TypeError, ValueError):
        pass  # No such call off macOS; the new file then has the mode only.
    finally:
        _close(from_fd)


def _write_temp(dir_fd: int, name: str, data: bytes, before: os.stat_result) -> str:
    """A new file beside the original holding data, with its access settings, flushed to disk; returns its name."""
    temp_name = ".%s.unbound-fix-%s.tmp" % (name, os.urandom(8).hex())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, stat.S_IMODE(before.st_mode))
        _copy_access(dir_fd, name, fd, before.st_gid)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except BaseException:
        _close(fd)
        _remove(dir_fd, temp_name)
        raise
    _close(fd)
    return temp_name


def _exchange(dir_fd: int, first: str, second: str) -> bool:
    """Swaps two names in the folder in one atomic step where the OS can (macOS); False when it cannot."""
    try:
        import ctypes
        swap = ctypes.CDLL(None, use_errno=True).renameatx_np
        swap.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        return swap(dir_fd, os.fsencode(first), dir_fd, os.fsencode(second), _RENAME_SWAP) == 0
    except (ImportError, OSError, AttributeError, TypeError, ValueError):
        return False


def _identity(dir_fd: int, name: str) -> Optional[Tuple[int, int]]:
    try:
        now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None
    return now.st_dev, now.st_ino


def _put_back(dir_fd: int, temp_name: str, name: str, placed: Optional[Tuple[int, int]]) -> None:
    """Swaps the newer save held under the temp name back in, and again if yet another save landed meanwhile."""
    for _ in range(_SWAP_BACK_TRIES):
        placing = _identity(dir_fd, temp_name)
        if not _exchange(dir_fd, temp_name, name):
            break
        # Getting back what was last put there means nothing was saved in between: the newest save is in place.
        if _identity(dir_fd, temp_name) == placed:
            return
        placed = placing
    os.rename(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def _put_in_place(dir_fd: int, temp_name: str, name: str, before: os.stat_result) -> None:
    """Makes the temp file the file, and undoes it if someone saved the file in that instant."""
    mine = _identity(dir_fd, temp_name)
    if not _exchange(dir_fd, temp_name, name):
        os.rename(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return
    # The swap is atomic, so the temp name now holds exactly what the fix replaced.
    if _still_same(dir_fd, temp_name, before):
        _remove(dir_fd, temp_name)
        return
    # A newer save was swapped out: it goes back, and the fix waits for the next scan.
    _put_back(dir_fd, temp_name, name, mine)
    raise _Failed("changed")


def _swap_in(dir_fd: int, name: str, data: bytes, before: os.stat_result) -> None:
    """Replaces the file with data by temp file and rename, unless it changed since it was read."""
    try:
        temp_name = _write_temp(dir_fd, name, data, before)
    except OSError as error:
        raise _Failed("refused") from error
    try:
        if not _still_same(dir_fd, name, before):
            raise _Failed("changed")
        _put_in_place(dir_fd, temp_name, name, before)
    except BaseException as error:
        _remove(dir_fd, temp_name)
        if isinstance(error, OSError):
            raise _Failed("refused") from error
        raise
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # The rename is done; a folder that cannot be flushed does not undo it.


def fix_file(file_path: str, memory_root: str, sha256: str, spans: List) -> Dict:
    """The outcome of removing spans from the file, which must still hash to sha256."""
    dir_fd = None
    try:
        parts = _contained_parts(file_path, memory_root)
        dir_fd = _open_parent(parts)
        data, before = _read_file(dir_fd, parts[-1])
        if hashlib.sha256(data).hexdigest() != sha256.lower():
            raise _Failed("changed")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _Failed("not_utf8") from error
        if not _valid_spans(spans, len(text)):
            raise _Failed("refused")
        fixed = _replaced(text, spans).encode("utf-8")
        # Checked before the write: past the cap the scan would stop reading the file in full.
        if len(fixed) > MAX_FILE_BYTES:
            raise _Failed("too_large")
        _swap_in(dir_fd, parts[-1], fixed, before)
        if _read_file(dir_fd, parts[-1])[0] != fixed:
            raise _Failed("changed")
        return {"outcome": "fixed"}
    except _Failed as failed:
        return {"outcome": "failed", "reason": failed.reason}
    finally:
        _close(dir_fd)


def _fix_request(request: Dict) -> Optional[Tuple[str, str, str, List]]:
    """(file_path, memory_root, sha256, spans) of a well-formed fix request, else None."""
    file_path, memory_root = request.get("file_path"), request.get("memory_root")
    sha256, spans = request.get("sha256"), request.get("spans")
    if not (isinstance(file_path, str) and isinstance(memory_root, str) and isinstance(spans, list)
            and isinstance(sha256, str) and _SHA256.fullmatch(sha256)):
        return None
    if "\x00" in file_path or "\x00" in memory_root:
        return None
    return file_path, memory_root, sha256, spans


def _answer(request: object) -> Optional[Dict]:
    if not isinstance(request, dict):
        return None
    if request.get("op") == "ping":
        return {"ok": True, "helper": "memory-fix", "protocol": PROTOCOL}
    fix = _fix_request(request) if request.get("op") == "fix" else None
    return fix_file(*fix) if fix else None


def main() -> int:
    """Reads one JSON request on stdin and writes one JSON line on stdout; 2 for a malformed request."""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return 2
        try:
            request = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            return 2
        answer = _answer(request)
        if answer is None:
            return 2
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()
        return 0
    except Exception:  # pylint: disable=broad-except
        # Says nothing, so the agent posts no result and the fix is handed out again on the next run.
        return 1


if __name__ == "__main__":
    sys.exit(main())
