"""Read-only lookup of one key in a Chromium Local Storage LevelDB directory."""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

FOUND = "found"
NOT_FOUND = "not_found"
ABSENT = "absent"
UNREADABLE = "unreadable"
TOO_LARGE = "too_large"
CORRUPT = "corrupt"

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_DIR_BYTES = 128 * 1024 * 1024
MAX_DIR_ENTRIES = 10_000
MAX_BLOCK_BYTES = 8 * 1024 * 1024
_READ_ATTEMPTS = 3

_TABLE_MAGIC = 0xDB4775248B80FB57
_FOOTER_LEN = 48
_BLOCK_TRAILER_LEN = 5
_LOG_BLOCK_LEN = 32768
_LOG_HEADER_LEN = 7
_LOG_FULL, _LOG_FIRST, _LOG_MIDDLE, _LOG_LAST = 1, 2, 3, 4
_TYPE_DELETION, _TYPE_VALUE = 0, 1
_BYTEWISE_COMPARATOR = b"leveldb.BytewiseComparator"
_MANIFEST_NAME = re.compile(r"MANIFEST-(\d{1,20})")
_NUMBERED_FILE = re.compile(r"(\d{1,20})\.(ldb|sst|log)")


class _Corrupt(Exception):
    pass


class _TooLarge(Exception):
    pass


def _varint(data: bytes, pos: int, max_bytes: int = 10) -> Tuple[int, int]:
    if pos < len(data) and data[pos] < 0x80:
        return data[pos], pos + 1
    result = 0
    for index in range(max_bytes):
        if pos + index >= len(data):
            raise _Corrupt("truncated varint")
        byte = data[pos + index]
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, pos + index + 1
    raise _Corrupt("varint too long")


def _length_prefixed(data: bytes, pos: int) -> Tuple[bytes, int]:
    length, pos = _varint(data, pos, 5)
    if pos + length > len(data):
        raise _Corrupt("slice overruns buffer")
    return data[pos:pos + length], pos + length


def _snappy_decompress(data: bytes) -> bytes:
    expected_len, pos = _varint(data, 0, 5)
    if expected_len > MAX_BLOCK_BYTES:
        raise _Corrupt("snappy block too large")
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            literal_len = tag >> 2
            if literal_len >= 60:
                width = literal_len - 59
                if pos + width > len(data):
                    raise _Corrupt("truncated snappy literal length")
                literal_len = int.from_bytes(data[pos:pos + width], "little")
                pos += width
            literal_len += 1
            if pos + literal_len > len(data) or len(out) + literal_len > expected_len:
                raise _Corrupt("snappy literal overruns")
            out += data[pos:pos + literal_len]
            pos += literal_len
            continue
        if kind == 1:
            if pos + 1 > len(data):
                raise _Corrupt("truncated snappy copy")
            copy_len = 4 + ((tag >> 2) & 7)
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        else:
            width = 2 if kind == 2 else 4
            if pos + width > len(data):
                raise _Corrupt("truncated snappy copy")
            copy_len = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + width], "little")
            pos += width
        if offset == 0 or offset > len(out) or len(out) + copy_len > expected_len:
            raise _Corrupt("snappy copy out of range")
        start = len(out) - offset
        if offset >= copy_len:
            out += out[start:start + copy_len]
        else:
            pattern = bytes(out[start:])
            out += (pattern * (copy_len // offset + 1))[:copy_len]
    if len(out) != expected_len:
        raise _Corrupt("snappy length mismatch")
    return bytes(out)


def _read_block(data: bytes, offset: int, size: int) -> bytes:
    if offset + size + _BLOCK_TRAILER_LEN > len(data) - _FOOTER_LEN or size > MAX_BLOCK_BYTES:
        raise _Corrupt("block handle out of range")
    content = data[offset:offset + size]
    compression = data[offset + size]
    if compression == 0:
        return content
    if compression == 1:
        return _snappy_decompress(content)
    raise _Corrupt("unsupported block compression")


def _block_entries(block: bytes) -> Iterator[Tuple[bytes, bytes]]:
    if len(block) < 4:
        raise _Corrupt("block too short")
    restart_count = int.from_bytes(block[-4:], "little")
    entries_end = len(block) - 4 * (restart_count + 1)
    if restart_count == 0 or entries_end < 0:
        raise _Corrupt("bad restart array")
    pos = 0
    key = b""
    while pos < entries_end:
        shared, pos = _varint(block, pos, 5)
        non_shared, pos = _varint(block, pos, 5)
        value_len, pos = _varint(block, pos, 5)
        if shared > len(key) or pos + non_shared + value_len > entries_end:
            raise _Corrupt("block entry overruns")
        key = key[:shared] + block[pos:pos + non_shared]
        pos += non_shared
        yield key, block[pos:pos + value_len]
        pos += value_len


def _table_versions(data: bytes, user_keys: Set[bytes]) -> Iterator[Tuple[int, int, bytes]]:
    if len(data) < _FOOTER_LEN or int.from_bytes(data[-8:], "little") != _TABLE_MAGIC:
        raise _Corrupt("not a table file")
    footer = data[-_FOOTER_LEN:]
    _, pos = _varint(footer, 0)
    _, pos = _varint(footer, pos)
    index_offset, pos = _varint(footer, pos)
    index_size, _ = _varint(footer, pos)
    previous_user_key: Optional[bytes] = None
    for separator, handle in _block_entries(_read_block(data, index_offset, index_size)):
        separator_user_key = separator[:-8]
        may_hold_key = any(
            (previous_user_key is None or previous_user_key <= user_key) and user_key <= separator_user_key
            for user_key in user_keys
        )
        previous_user_key = separator_user_key
        if not may_hold_key:
            continue
        block_offset, handle_pos = _varint(handle, 0)
        block_size, _ = _varint(handle, handle_pos)
        for internal_key, value in _block_entries(_read_block(data, block_offset, block_size)):
            if len(internal_key) < 8 or internal_key[:-8] not in user_keys:
                continue
            tag = int.from_bytes(internal_key[-8:], "little")
            yield tag >> 8, tag & 0xFF, value


def _log_records(data: bytes) -> Iterator[bytes]:
    pending: Optional[bytearray] = None
    for block_start in range(0, len(data), _LOG_BLOCK_LEN):
        block = data[block_start:block_start + _LOG_BLOCK_LEN]
        is_last_block = block_start + _LOG_BLOCK_LEN >= len(data)
        pos = 0
        while pos + _LOG_HEADER_LEN <= len(block):
            length = int.from_bytes(block[pos + 4:pos + 6], "little")
            record_type = block[pos + 6]
            if record_type == 0 and length == 0:
                break
            fragment_end = pos + _LOG_HEADER_LEN + length
            if fragment_end > len(block):
                if is_last_block:
                    return
                raise _Corrupt("log fragment overruns block")
            fragment = block[pos + _LOG_HEADER_LEN:fragment_end]
            pos = fragment_end
            if record_type == _LOG_FULL:
                pending = None
                yield fragment
            elif record_type == _LOG_FIRST:
                pending = bytearray(fragment)
            elif record_type in (_LOG_MIDDLE, _LOG_LAST):
                if pending is None:
                    continue
                pending += fragment
                if record_type == _LOG_LAST:
                    yield bytes(pending)
                    pending = None
            else:
                raise _Corrupt("unknown log record type")


def _batch_versions(batch: bytes, user_keys: Set[bytes]) -> Iterator[Tuple[int, int, bytes]]:
    if len(batch) < 12:
        raise _Corrupt("write batch too short")
    sequence = int.from_bytes(batch[:8], "little")
    count = int.from_bytes(batch[8:12], "little")
    pos = 12
    for index in range(count):
        if pos >= len(batch):
            raise _Corrupt("write batch shorter than its count")
        op_type = batch[pos]
        key, pos = _length_prefixed(batch, pos + 1)
        if op_type == _TYPE_VALUE:
            value, pos = _length_prefixed(batch, pos)
        elif op_type == _TYPE_DELETION:
            value = b""
        else:
            raise _Corrupt("unknown write batch op")
        if key in user_keys:
            yield sequence + index, op_type, value


def _live_files(manifest: bytes) -> Tuple[Set[int], int, int]:
    live_tables: Set[int] = set()
    log_number = 0
    prev_log_number = 0
    for edit in _log_records(manifest):
        pos = 0
        while pos < len(edit):
            tag, pos = _varint(edit, pos, 5)
            if tag == 1:
                comparator, pos = _length_prefixed(edit, pos)
                if comparator != _BYTEWISE_COMPARATOR:
                    raise _Corrupt("unsupported comparator")
            elif tag == 2:
                log_number, pos = _varint(edit, pos)
            elif tag in (3, 4):
                _, pos = _varint(edit, pos)
            elif tag == 5:
                _, pos = _varint(edit, pos, 5)
                _, pos = _length_prefixed(edit, pos)
            elif tag == 6:
                _, pos = _varint(edit, pos, 5)
                file_number, pos = _varint(edit, pos)
                live_tables.discard(file_number)
            elif tag == 7:
                _, pos = _varint(edit, pos, 5)
                file_number, pos = _varint(edit, pos)
                _, pos = _varint(edit, pos)
                _, pos = _length_prefixed(edit, pos)
                _, pos = _length_prefixed(edit, pos)
                live_tables.add(file_number)
            elif tag == 9:
                prev_log_number, pos = _varint(edit, pos)
            else:
                raise _Corrupt("unknown manifest tag")
    return live_tables, log_number, prev_log_number


def _read_file(path: Path) -> bytes:
    with open(path, "rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise _TooLarge(path.name)
    return data


def _list_files(leveldb_dir: Path) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    total = 0
    with os.scandir(leveldb_dir) as entries:
        for entry in entries:
            if len(sizes) >= MAX_DIR_ENTRIES:
                raise _TooLarge("too many entries")
            if not entry.is_file(follow_symlinks=False):
                continue
            size = entry.stat(follow_symlinks=False).st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_DIR_BYTES:
                raise _TooLarge(entry.name)
            sizes[entry.name] = size
    return sizes


def _newest_version(leveldb_dir: Path, user_keys: Set[bytes]) -> Optional[Tuple[int, int, bytes]]:
    file_sizes = _list_files(leveldb_dir)
    if "CURRENT" not in file_sizes:
        raise _Corrupt("no CURRENT file")
    manifest_match = _MANIFEST_NAME.fullmatch(_read_file(leveldb_dir / "CURRENT").decode("latin-1").strip())
    if not manifest_match:
        raise _Corrupt("CURRENT does not name a manifest")
    live_tables, log_number, prev_log_number = _live_files(_read_file(leveldb_dir / manifest_match.group(0)))

    table_names: Dict[int, str] = {}
    log_names: List[str] = []
    for name in file_sizes:
        numbered = _NUMBERED_FILE.fullmatch(name)
        if not numbered:
            continue
        file_number = int(numbered.group(1))
        if numbered.group(2) == "log":
            if file_number >= log_number or (prev_log_number and file_number == prev_log_number):
                log_names.append(name)
        elif file_number in live_tables:
            table_names[file_number] = name
    if set(table_names) != live_tables:
        raise FileNotFoundError("live table missing")

    newest: Optional[Tuple[int, int, bytes]] = None
    for name in list(table_names.values()) + log_names:
        data = _read_file(leveldb_dir / name)
        if name.endswith(".log"):
            versions = (version for record in _log_records(data) for version in _batch_versions(record, user_keys))
        else:
            versions = _table_versions(data, user_keys)
        for version in versions:
            if newest is None or version[0] > newest[0]:
                newest = version
    return newest


def _decode_value(raw: bytes) -> Optional[str]:
    if not raw:
        return None
    try:
        if raw[0] == 0:
            return raw[1:].decode("utf-16-le")
        if raw[0] == 1:
            return raw[1:].decode("latin-1")
    except UnicodeDecodeError:
        return None
    return None


def _encoded_keys(origin: str, key: str) -> Set[bytes]:
    prefix = b"_" + origin.encode("utf-8") + b"\x00"
    encoded = {prefix + b"\x00" + key.encode("utf-16-le")}
    try:
        encoded.add(prefix + b"\x01" + key.encode("latin-1"))
    except UnicodeEncodeError:
        pass
    return encoded


def read_local_storage_value(leveldb_dir: Path, origin: str, key: str) -> Tuple[str, Optional[str]]:
    """Return (state, value) for one origin's key; value is set only when state is FOUND."""
    try:
        if not leveldb_dir.is_dir():
            return ABSENT, None
        user_keys = _encoded_keys(origin, key)
        for attempt in range(_READ_ATTEMPTS):
            try:
                newest = _newest_version(leveldb_dir, user_keys)
                break
            except FileNotFoundError:
                logger.debug("Local Storage changed during read (attempt %d)", attempt + 1)
        else:
            return UNREADABLE, None
        if newest is None or newest[1] == _TYPE_DELETION:
            return NOT_FOUND, None
        value = _decode_value(newest[2])
        return (FOUND, value) if value is not None else (CORRUPT, None)
    except PermissionError:
        return UNREADABLE, None
    except _TooLarge:
        return TOO_LARGE, None
    except (_Corrupt, IndexError, ValueError):
        return CORRUPT, None
    except OSError:
        return UNREADABLE, None
    except Exception as e:
        logger.debug("Unexpected error reading Local Storage: %s", type(e).__name__)
        return CORRUPT, None
