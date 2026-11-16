import json
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.chromium_local_storage as ls
import scripts.coding_discovery_tools.mcp_extraction_helpers as helpers
from scripts.coding_discovery_tools import utils
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector

ORIGIN = "https://claude.ai"
KEY = "claudeai.mcpBootstrapSnapshot.v1"
VALUE, DELETION = 1, 0


def _varint(number: int) -> bytes:
    out = bytearray()
    while number >= 0x80:
        out.append((number & 0x7F) | 0x80)
        number >>= 7
    out.append(number)
    return bytes(out)


def _prefixed(data: bytes) -> bytes:
    return _varint(len(data)) + data


def _storage_key(origin: str, key: str, utf16: bool = False) -> bytes:
    encoded = b"\x00" + key.encode("utf-16-le") if utf16 else b"\x01" + key.encode("latin-1")
    return b"_" + origin.encode() + b"\x00" + encoded


def _storage_value(text: str, utf16: bool = False) -> bytes:
    return b"\x00" + text.encode("utf-16-le") if utf16 else b"\x01" + text.encode("latin-1")


def _snappy_literal(data: bytes) -> bytes:
    out = bytearray(_varint(len(data)))
    for start in range(0, len(data), 65536):
        chunk = data[start:start + 65536]
        if len(chunk) <= 60:
            out.append((len(chunk) - 1) << 2)
        else:
            out.append(61 << 2)
            out += (len(chunk) - 1).to_bytes(2, "little")
        out += chunk
    return bytes(out)


def _block(entries) -> bytes:
    out = bytearray()
    previous = b""
    for key, value in entries:
        shared = 0
        while shared < min(len(key), len(previous)) and key[shared] == previous[shared]:
            shared += 1
        out += _varint(shared) + _varint(len(key) - shared) + _varint(len(value)) + key[shared:] + value
        previous = key
    return bytes(out) + (0).to_bytes(4, "little") + (1).to_bytes(4, "little")


def _internal_key(user_key: bytes, sequence: int, kind: int) -> bytes:
    return user_key + ((sequence << 8) | kind).to_bytes(8, "little")


def _table_bytes(records, snappy: bool = False, entries_per_block: int = 2) -> bytes:
    """records: (user_key, sequence, kind, value)."""
    ordered = sorted(records, key=lambda r: (r[0], -r[1]))
    entries = [(_internal_key(u, s, k), v) for u, s, k, v in ordered]
    out = bytearray()

    def write_block(content: bytes) -> bytes:
        payload, kind = (_snappy_literal(content), 1) if snappy else (content, 0)
        handle = _varint(len(out)) + _varint(len(payload))
        out.extend(payload + bytes([kind]) + b"\0\0\0\0")
        return handle

    index_entries = []
    for start in range(0, len(entries), entries_per_block):
        chunk = entries[start:start + entries_per_block]
        index_entries.append((chunk[-1][0], write_block(_block(chunk))))
    metaindex_handle = write_block(_block([]))
    index_handle = write_block(_block(index_entries))
    footer = (metaindex_handle + index_handle).ljust(40, b"\0")
    return bytes(out) + footer + (0xDB4775248B80FB57).to_bytes(8, "little")


def _log_bytes(payloads) -> bytes:
    out = bytearray()
    for payload in payloads:
        remaining = payload
        first = True
        while True:
            space = 32768 - len(out) % 32768
            if space < 7:
                out += b"\0" * space
                space = 32768
            fragment, remaining = remaining[:space - 7], remaining[space - 7:]
            if first and not remaining:
                kind = 1
            elif first:
                kind = 2
            elif remaining:
                kind = 3
            else:
                kind = 4
            out += b"\0\0\0\0" + len(fragment).to_bytes(2, "little") + bytes([kind]) + fragment
            first = False
            if not remaining:
                break
    return bytes(out)


def _batch(sequence: int, ops) -> bytes:
    out = bytearray(sequence.to_bytes(8, "little") + len(ops).to_bytes(4, "little"))
    for kind, key, value in ops:
        out += bytes([kind]) + _prefixed(key)
        if kind == VALUE:
            out += _prefixed(value)
    return bytes(out)


def _write_store(directory: Path, tables=(), batches=(), snappy: bool = False, log_number: int = 100) -> Path:
    """tables: lists of (user_key, seq, kind, value); batches: (seq, [(kind, key, value)])."""
    directory.mkdir(parents=True, exist_ok=True)
    edit = bytearray(_prefixed(b"leveldb.BytewiseComparator"))
    edit = bytearray(_varint(1)) + edit + _varint(2) + _varint(log_number) + _varint(3) + _varint(log_number + 1)
    for number, records in enumerate(tables, start=10):
        data = _table_bytes(records, snappy=snappy)
        (directory / f"{number:06d}.ldb").write_bytes(data)
        edit += _varint(7) + _varint(0) + _varint(number) + _varint(len(data)) + _prefixed(b"a") + _prefixed(b"z")
    (directory / "MANIFEST-000001").write_bytes(_log_bytes([bytes(edit)]))
    (directory / "CURRENT").write_bytes(b"MANIFEST-000001\n")
    (directory / "LOCK").write_bytes(b"")
    (directory / f"{log_number:06d}.log").write_bytes(_log_bytes([_batch(s, ops) for s, ops in batches]))
    return directory


def _read(directory: Path):
    return ls.read_local_storage_value(directory, ORIGIN, KEY)


TARGET = _storage_key(ORIGIN, KEY)
OTHER = _storage_key(ORIGIN, "unrelated")


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / "leveldb"

    def test_value_found_in_table(self):
        _write_store(self.store, tables=[[(TARGET, 5, VALUE, _storage_value("one")), (OTHER, 6, VALUE, b"\x01x")]])
        self.assertEqual(_read(self.store), (ls.FOUND, "one"))

    def test_newer_log_value_wins(self):
        _write_store(self.store, tables=[[(TARGET, 5, VALUE, _storage_value("old"))]],
                     batches=[(9, [(VALUE, OTHER, b"\x01x"), (VALUE, TARGET, _storage_value("new"))])])
        self.assertEqual(_read(self.store), (ls.FOUND, "new"))

    def test_older_log_value_loses_to_table(self):
        _write_store(self.store, tables=[[(TARGET, 50, VALUE, _storage_value("table"))]],
                     batches=[(9, [(VALUE, TARGET, _storage_value("log"))])])
        self.assertEqual(_read(self.store), (ls.FOUND, "table"))

    def test_newer_deletion_hides_key(self):
        _write_store(self.store, tables=[[(TARGET, 5, VALUE, _storage_value("old"))]],
                     batches=[(9, [(DELETION, TARGET, b"")])])
        self.assertEqual(_read(self.store), (ls.NOT_FOUND, None))

    def test_deletion_in_table_hides_older_value(self):
        _write_store(self.store, tables=[[(TARGET, 5, VALUE, _storage_value("old")), (TARGET, 7, DELETION, b"")]])
        self.assertEqual(_read(self.store), (ls.NOT_FOUND, None))

    def test_highest_sequence_wins_across_tables(self):
        _write_store(self.store, tables=[
            [(TARGET, 3, VALUE, _storage_value("a"))],
            [(TARGET, 12, VALUE, _storage_value("c"))],
            [(TARGET, 8, VALUE, _storage_value("b"))],
        ])
        self.assertEqual(_read(self.store), (ls.FOUND, "c"))

    def test_table_not_in_manifest_is_ignored(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("live"))]])
        (self.store / "000099.ldb").write_bytes(_table_bytes([(TARGET, 90, VALUE, _storage_value("stray"))]))
        (self.store / "000098.ldb").write_bytes(b"")
        self.assertEqual(_read(self.store), (ls.FOUND, "live"))

    def test_obsolete_log_is_ignored(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("live"))]])
        (self.store / "000042.log").write_bytes(_log_bytes([_batch(90, [(VALUE, TARGET, _storage_value("old log"))])]))
        self.assertEqual(_read(self.store), (ls.FOUND, "live"))

    def test_utf16_value_and_key(self):
        utf16_key = _storage_key(ORIGIN, KEY, utf16=True)
        _write_store(self.store, tables=[[(utf16_key, 5, VALUE, _storage_value("ü✓", utf16=True))]])
        self.assertEqual(_read(self.store), (ls.FOUND, "ü✓"))

    def test_latin1_value(self):
        _write_store(self.store, tables=[[(TARGET, 5, VALUE, _storage_value("café"))]])
        self.assertEqual(_read(self.store), (ls.FOUND, "café"))

    def test_other_origin_is_not_matched(self):
        _write_store(self.store, tables=[[(_storage_key("https://evil.example", KEY), 5, VALUE, b"\x01x")]])
        self.assertEqual(_read(self.store), (ls.NOT_FOUND, None))

    def test_snappy_blocks(self):
        records = [(TARGET, 5, VALUE, _storage_value("x" * 5000))] + [
            (_storage_key(ORIGIN, f"k{i}"), i, VALUE, b"\x01v") for i in range(20)
        ]
        _write_store(self.store, tables=[records], snappy=True)
        self.assertEqual(_read(self.store), (ls.FOUND, "x" * 5000))

    def test_value_spanning_log_blocks(self):
        big = "y" * 70000
        _write_store(self.store, batches=[(4, [(VALUE, TARGET, _storage_value(big))])])
        self.assertEqual(_read(self.store), (ls.FOUND, big))

    def test_torn_log_tail_is_ignored(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("table"))]],
                     batches=[(9, [(VALUE, TARGET, _storage_value("newer"))])])
        log = self.store / "000100.log"
        log.write_bytes(log.read_bytes()[:-3])
        self.assertEqual(_read(self.store), (ls.FOUND, "table"))

    def test_missing_directory(self):
        self.assertEqual(_read(self.store), (ls.ABSENT, None))

    def test_empty_directory_is_corrupt(self):
        self.store.mkdir()
        self.assertEqual(_read(self.store), (ls.CORRUPT, None))

    def test_zero_length_live_table_is_corrupt(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        (self.store / "000010.ldb").write_bytes(b"")
        self.assertEqual(_read(self.store), (ls.CORRUPT, None))

    def test_garbage_files_are_corrupt(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        for path in self.store.iterdir():
            path.write_bytes(b"not leveldb at all \xff\xfe" * 7)
        self.assertEqual(_read(self.store), (ls.CORRUPT, None))

    def test_truncated_table_is_corrupt(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        table = self.store / "000010.ldb"
        table.write_bytes(table.read_bytes()[:-20])
        self.assertEqual(_read(self.store), (ls.CORRUPT, None))

    def test_unknown_comparator_is_corrupt(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        edit = _varint(1) + _prefixed(b"idb_cmp1") + _varint(2) + _varint(100)
        (self.store / "MANIFEST-000001").write_bytes(_log_bytes([edit]))
        self.assertEqual(_read(self.store), (ls.CORRUPT, None))

    def test_file_too_large(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x" * 4000))]])
        with mock.patch.object(ls, "MAX_FILE_BYTES", 1000):
            self.assertEqual(_read(self.store), (ls.TOO_LARGE, None))

    def test_directory_too_large(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x" * 400))]] * 4)
        with mock.patch.object(ls, "MAX_DIR_BYTES", 1000):
            self.assertEqual(_read(self.store), (ls.TOO_LARGE, None))

    def test_permission_denied(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        with mock.patch.object(ls, "_read_file", side_effect=PermissionError("denied")):
            self.assertEqual(_read(self.store), (ls.UNREADABLE, None))

    def test_file_vanishing_once_is_retried(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        real_read = ls._read_file
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) == 3:
                raise FileNotFoundError(path)
            return real_read(path)

        with mock.patch.object(ls, "_read_file", side_effect=flaky):
            self.assertEqual(_read(self.store), (ls.FOUND, "x"))

    def test_file_always_vanishing_is_unreadable(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        with mock.patch.object(ls, "_read_file", side_effect=FileNotFoundError("gone")):
            self.assertEqual(_read(self.store), (ls.UNREADABLE, None))

    def test_live_table_missing_from_disk_is_unreadable(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        (self.store / "000010.ldb").unlink()
        self.assertEqual(_read(self.store), (ls.UNREADABLE, None))

    def test_reader_never_opens_for_write_or_touches_lock(self):
        _write_store(self.store, tables=[[(TARGET, 3, VALUE, _storage_value("x"))]])
        real_open = open
        opened = []

        def tracking_open(path, mode="r", *args, **kwargs):
            opened.append((Path(path).name, mode))
            return real_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=tracking_open):
            _read(self.store)
        self.assertTrue(opened)
        self.assertTrue(all(mode == "rb" for _, mode in opened))
        self.assertNotIn("LOCK", [name for name, _ in opened])

    def test_malformed_mutations_never_raise(self):
        _write_store(self.store, tables=[
            [(TARGET, 3, VALUE, _storage_value("a" * 300))] + [(OTHER, 4, VALUE, b"\x01b")],
            [(TARGET, 8, VALUE, _storage_value("c" * 300))],
        ], batches=[(20, [(VALUE, TARGET, _storage_value("d" * 100))])], snappy=True)
        pristine = {path.name: path.read_bytes() for path in self.store.iterdir()}
        rng = random.Random(1234)
        allowed = {ls.FOUND, ls.NOT_FOUND, ls.UNREADABLE, ls.CORRUPT, ls.TOO_LARGE}
        started = time.monotonic()
        for _ in range(300):
            name = rng.choice(sorted(pristine))
            data = bytearray(pristine[name])
            mutation = rng.randrange(4)
            if mutation == 0 and data:
                for _ in range(rng.randint(1, 8)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
            elif mutation == 1:
                data = data[:rng.randrange(len(data) + 1)]
            elif mutation == 2:
                position = rng.randrange(len(data) + 1)
                data[position:position] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 64)))
            else:
                data = bytearray(rng.randrange(256) for _ in range(rng.randint(0, 200)))
            (self.store / name).write_bytes(bytes(data))
            state, value = _read(self.store)
            self.assertIn(state, allowed)
            self.assertEqual(value is not None, state == ls.FOUND)
            (self.store / name).write_bytes(pristine[name])
        self.assertLess(time.monotonic() - started, 30)


class SnappyTest(unittest.TestCase):
    def test_every_copy_form(self):
        stream = bytes([18, (3 << 2), *b"abcd", 0x11, 4, 0x0E, 12, 0, 0x07, 1, 0, 0, 0])
        self.assertEqual(ls._snappy_decompress(stream), b"abcdabcdabcdabcddd")

    def test_long_literal_form(self):
        data = bytes(range(256)) * 3
        self.assertEqual(ls._snappy_decompress(_snappy_literal(data)), data)

    def test_rejects_zero_offset(self):
        with self.assertRaises(ls._Corrupt):
            ls._snappy_decompress(bytes([8, (3 << 2), *b"abcd", 0x01, 0]))

    def test_rejects_offset_beyond_output(self):
        with self.assertRaises(ls._Corrupt):
            ls._snappy_decompress(bytes([8, (3 << 2), *b"abcd", 0x01, 5]))

    def test_rejects_output_beyond_declared_size(self):
        with self.assertRaises(ls._Corrupt):
            ls._snappy_decompress(bytes([5, (3 << 2), *b"abcd", 0x01, 4]))

    def test_rejects_short_output(self):
        with self.assertRaises(ls._Corrupt):
            ls._snappy_decompress(bytes([9, (3 << 2), *b"abcd"]))


def _snapshot(servers, tools_by_server, version=1, saved_at=1_700_000_000_000):
    return json.dumps({
        "version": version, "orgUuid": "org", "accountUuid": "acct", "savedAt": saved_at,
        "servers": servers, "toolsByServer": tools_by_server,
    })


LINEAR = {"uuid": "u-linear", "name": "Linear", "url": "https://mcp.linear.app/mcp", "cai_flags": []}
NOTION = {"uuid": "u-notion", "name": "Notion", "url": "https://mcp.notion.com/mcp", "cai_flags": []}
HIDDEN = {"uuid": "u-docs", "name": "Claude Docs", "url": "https://docs", "cai_flags": ["hide_from_connectors_list"]}
LINEAR_TOOLS = [{"name": "list_issues", "description": "d", "inputSchema": {}, "enabledKey": "x", "_meta": {}}]


def _write_snapshot_store(home: Path, snapshot_text: str) -> None:
    store = helpers._claude_app_support_dir(home) / "Local Storage" / "leveldb"
    _write_store(store, tables=[[(TARGET, 5, VALUE, _storage_value(snapshot_text))]], snappy=True)


class SnapshotParsingTest(unittest.TestCase):
    def test_visible_servers_in_cli_shape(self):
        parsed = helpers._parse_claudeai_snapshot(_snapshot([LINEAR, HIDDEN, NOTION], {"u-linear": LINEAR_TOOLS}))
        servers = helpers._claudeai_snapshot_servers(parsed)
        self.assertEqual(servers, [
            {"name": "claude.ai Linear", "scope": "claudeai", "scan": {
                "scanned_at": "2023-11-14T22:13:20+00:00",
                "tools": [{"name": "list_issues", "description": "d", "inputSchema": {}}],
                "tool_count": 1, "server_info": None, "error": None,
            }},
            {"name": "claude.ai Notion", "scope": "claudeai"},
        ])
        for server in servers:
            self.assertLessEqual(set(server), {"name", "scope", "scan"})

    def test_unsupported_shapes(self):
        for text in (
            _snapshot([LINEAR], {}, version=2),
            _snapshot([LINEAR], {}, version=True),
            _snapshot([LINEAR], {}, saved_at="yesterday"),
            _snapshot([LINEAR], {}, saved_at=10 ** 30),
            _snapshot([{"name": "Linear"}], {}),
            _snapshot([{"uuid": "u", "name": "X", "cai_flags": "hide"}], {}),
            _snapshot("Linear", {}),
            json.dumps({"version": 1, "savedAt": 1, "servers": []}),
            json.dumps([1, 2]),
            "{not json",
        ):
            self.assertIsNone(helpers._parse_claudeai_snapshot(text), text)

    def test_probe_state_and_age_bucket(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("platform.system", return_value="Darwin"):
            home = Path(tmp)
            self.assertEqual(helpers.read_claudeai_snapshot(home), ("absent", None))
            recent_ms = int(time.time() * 1000) - 3 * 86_400_000
            _write_snapshot_store(home, _snapshot([LINEAR], {}, saved_at=recent_ms))
            state, snapshot = helpers.read_claudeai_snapshot(home)
            self.assertEqual(state, "usable_lt7d")
            self.assertIsNotNone(snapshot)
            _write_snapshot_store(home, _snapshot([LINEAR], {}, version=9))
            self.assertEqual(helpers.read_claudeai_snapshot(home), ("unsupported", None))


class DesktopSurfaceSnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "me"
        self.home.mkdir()
        self.admin_homes = []
        for patcher in (
            mock.patch.object(utils, "_SENTRY_DSN", ""),
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch.object(helpers, "_iter_admin_user_homes", side_effect=lambda *_: list(self.admin_homes)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        utils._cowork_probes.clear()
        self.addCleanup(utils._cowork_probes.clear)
        self.detector = AIToolsDetector()
        self.detector._claude_desktop_mcp_extractor = None
        self.detector.extract_all_cowork_skills = lambda: {"user_skills": []}

    def _sign_in_cli(self, home: Path, names=("claude.ai Gmail", "claude.ai Linear")):
        (home / ".claude.json").write_text(json.dumps({
            "claudeAiMcpEverConnected": list(names), "oauthAccount": {"accountUuid": "acct"},
        }))
        config = helpers._claude_app_support_dir(home) / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"lastKnownAccountUuid": "acct"}))

    def _connector_projects(self, tool_name):
        result = self.detector.process_single_tool({"name": tool_name})
        return [p for p in result.get("projects", []) if any(s.get("scope") == "claudeai" for s in p["mcpServers"])]

    def _connector_names(self, tool_name):
        return [s["name"] for p in self._connector_projects(tool_name) for s in p["mcpServers"]
                if s.get("scope") == "claudeai"]

    def test_snapshot_without_cli_file_reports_connectors(self):
        _write_snapshot_store(self.home, _snapshot([LINEAR, HIDDEN, NOTION], {"u-linear": LINEAR_TOOLS}))
        for tool_name in ("Claude Cowork", "Claude Desktop"):
            projects = self._connector_projects(tool_name)
            self.assertEqual([p["path"] for p in projects], [str(self.home / ".claude")])
            self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["claude.ai Linear", "claude.ai Notion"])
            self.assertEqual(projects[0]["mcpServers"][0]["scan"]["tool_count"], 1)
            self.assertNotIn("url", json.dumps(projects))

    def test_snapshot_adds_only_missing_names(self):
        self._sign_in_cli(self.home)
        _write_snapshot_store(self.home, _snapshot([NOTION, LINEAR], {"u-linear": LINEAR_TOOLS}))
        for tool_name in ("Claude Cowork", "Claude Desktop"):
            projects = self._connector_projects(tool_name)
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["mcpServers"], [
                {"name": "claude.ai Gmail", "scope": "claudeai"},
                {"name": "claude.ai Linear", "scope": "claudeai"},
                {"name": "claude.ai Notion", "scope": "claudeai"},
            ])

    def test_match_is_case_insensitive_and_never_duplicates(self):
        self._sign_in_cli(self.home, ["claude.ai NOTION", "claude.ai linear"])
        _write_snapshot_store(self.home, _snapshot([NOTION, LINEAR, dict(NOTION, uuid="u-2")], {}))
        names = self._connector_names("Claude Desktop")
        self.assertEqual(names, ["claude.ai NOTION", "claude.ai linear"])
        self.assertEqual(len({name.lower() for name in names}), len(names))

    def test_cli_entry_kept_as_is_when_in_both(self):
        self._sign_in_cli(self.home)
        expected = self._connector_projects("Claude Desktop")
        _write_snapshot_store(self.home, _snapshot([LINEAR], {"u-linear": LINEAR_TOOLS}))
        self.assertEqual(self._connector_projects("Claude Desktop"), expected)

    def test_empty_or_stale_snapshot_hides_nothing(self):
        self._sign_in_cli(self.home)
        expected = self._connector_projects("Claude Desktop")
        for servers in ([HIDDEN], [], [LINEAR]):
            _write_snapshot_store(self.home, _snapshot(servers, {}, saved_at=1000))
            self.assertEqual(self._connector_projects("Claude Desktop"), expected)
            self.assertEqual(self._connector_projects("Claude Cowork"), expected)

    def test_snapshot_used_when_cli_account_differs(self):
        self._sign_in_cli(self.home)
        config = helpers._claude_app_support_dir(self.home) / "config.json"
        config.write_text(json.dumps({"lastKnownAccountUuid": "someone-else"}))
        _write_snapshot_store(self.home, _snapshot([NOTION], {}))
        self.assertEqual(self._connector_names("Claude Desktop"), ["claude.ai Notion"])

    def test_snapshot_used_when_cli_lists_no_connectors(self):
        self._sign_in_cli(self.home, names=())
        _write_snapshot_store(self.home, _snapshot([NOTION], {}))
        self.assertEqual(self._connector_names("Claude Cowork"), ["claude.ai Notion"])

    def test_unusable_snapshot_falls_back_to_cli_exactly(self):
        self._sign_in_cli(self.home)
        expected = self._connector_projects("Claude Desktop")
        self.assertEqual([s["name"] for p in expected for s in p["mcpServers"]],
                         ["claude.ai Gmail", "claude.ai Linear"])
        store = helpers._claude_app_support_dir(self.home) / "Local Storage" / "leveldb"
        for snapshot_text in (None, _snapshot([NOTION], {}, version=3)):
            shutil.rmtree(store, ignore_errors=True)
            if snapshot_text is None:
                store.mkdir(parents=True)
                (store / "CURRENT").write_bytes(b"\xff garbage")
                (store / "000003.ldb").write_bytes(b"\x00" * 100)
            else:
                _write_snapshot_store(self.home, snapshot_text)
            self.assertEqual(self._connector_projects("Claude Desktop"), expected)
            self.assertEqual(self._connector_projects("Claude Cowork"), expected)

    def test_each_home_is_evaluated_separately(self):
        alice, bob, carol = self.root / "alice", self.root / "bob", self.root / "carol"
        for home in (alice, bob, carol):
            home.mkdir()
        self.admin_homes = [alice, bob, carol]
        self._sign_in_cli(alice, ["claude.ai Gmail"])
        _write_snapshot_store(alice, _snapshot([LINEAR], {}))
        _write_snapshot_store(bob, _snapshot([LINEAR], {}))
        by_path = {p["path"]: [s["name"] for s in p["mcpServers"]] for p in self._connector_projects("Claude Desktop")}
        self.assertEqual(by_path, {
            str(alice / ".claude"): ["claude.ai Gmail", "claude.ai Linear"],
            str(bob / ".claude"): ["claude.ai Linear"],
        })

    def test_probe_recorded_when_snapshot_adds_nothing(self):
        self._sign_in_cli(self.home)
        _write_snapshot_store(self.home, _snapshot([LINEAR], {}, saved_at=1000))
        self._connector_projects("Claude Desktop")
        self.assertIn("snapshot:usable_ge30d", utils.cowork_probes())

    def test_probe_not_recorded_without_desktop_data_dir(self):
        self._connector_projects("Claude Desktop")
        self.assertFalse([p for p in utils.cowork_probes() if p.startswith("snapshot:")])

    def test_probe_records_unusable_state(self):
        store = helpers._claude_app_support_dir(self.home) / "Local Storage" / "leveldb"
        store.mkdir(parents=True)
        self._connector_projects("Claude Desktop")
        self.assertIn("snapshot:corrupt", utils.cowork_probes())

    def test_claude_code_output_unchanged(self):
        self._sign_in_cli(self.home)
        _write_snapshot_store(self.home, _snapshot([NOTION], {}))
        projects = []
        helpers.extract_claudeai_mcp_servers_with_root_support(projects)
        self.assertEqual(projects, [{
            "path": str(self.home / ".claude"),
            "mcpServers": [
                {"name": "claude.ai Gmail", "scope": "claudeai"},
                {"name": "claude.ai Linear", "scope": "claudeai"},
            ],
            "scope": "claudeai",
        }])


if __name__ == "__main__":
    unittest.main()
