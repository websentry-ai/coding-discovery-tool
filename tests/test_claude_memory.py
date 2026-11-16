"""Claude Code memory discovery: the scan-start gate, the folder walk, the secret check, its cache and the report."""

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.ai_tools_discovery as adm
import scripts.coding_discovery_tools.cache as cache_mod
import scripts.coding_discovery_tools.claude_memory_helpers as cmh
from scripts.coding_discovery_tools.constants import MAX_CONFIG_FILE_SIZE, MAX_SEARCH_DEPTH
from scripts.coding_discovery_tools.s3_uploader import compute_payload_hash
from tests.test_queue_drain_budget import _DrivesMain, _SimulatedClock

FAKE_SECRET = "postgres://admin:Hunter2pass@db.internal:5432/shop"
SLUG = "-work-shop"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _memory_dir(home, slug=SLUG):
    return home / ".claude" / "projects" / slug / "memory"


def _folder(memories, path):
    return next(folder for folder in memories["folders"] if folder["path"] == str(path))


class _MemoryHome(unittest.TestCase):
    """A temp home to scan and a separate temp state dir for the cache."""

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="memory-test-")).resolve()
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.home = self._dir / "home" / "alice"
        self.home.mkdir(parents=True)
        self.state_dir = self._dir / "state"
        self.state_dir.mkdir()
        for name in ("UNBOUND_DIR", "_HOME_STATE_DIR"):
            patcher = patch.object(cache_mod, name, self.state_dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cache_file = self.state_dir / cmh.MEMORY_CACHE_FILENAME

    def collect(self, home_user="alice"):
        return cmh.collect_claude_memories(self.home, home_user)

    def collect_recording_reads(self):
        """(memories, names of the files whose content was read)."""
        read_names = []
        real_read = cmh.read_rule_file_contained

        def _recording_read(path, *args, **kwargs):
            read_names.append(Path(path).name)
            return real_read(path, *args, **kwargs)

        with patch.object(cmh, "read_rule_file_contained", side_effect=_recording_read):
            return self.collect(), read_names


class TestMemoryDiscovery(_MemoryHome):

    def test_index_is_sent_clean_topics_are_counted_and_a_secret_is_flagged(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "# Index\n- [db](db.md)\n")
        _write(memory / "style.md", "Prefer small diffs.\n")
        _write(memory / "notes" / "deep.md", "Nested topic.\n")
        _write(memory / "db.md", "# Database\n\nurl: " + FAKE_SECRET + "\n")
        _write(memory.parent / "session.jsonl",
               json.dumps({"type": "summary"}) + "\n" + json.dumps({"cwd": "/work/shop"}) + "\n")

        memories = self.collect()

        self.assertEqual(len(memories["folders"]), 1)
        folder = memories["folders"][0]
        self.assertEqual(
            {key: folder[key] for key in ("path", "project_path", "memory_type", "scope")},
            {"path": str(memory), "project_path": "/work/shop", "memory_type": "auto", "scope": "user"},
        )
        self.assertNotIn("agent_name", folder)
        self.assertEqual(folder["index"]["file_name"], "MEMORY.md")
        self.assertEqual(folder["index"]["content"], "# Index\n- [db](db.md)\n")
        self.assertNotIn("findings", folder["index"])
        self.assertEqual(folder["counts"], {"total": 4, "flagged": 1, "clean": 3, "truncated": 0})
        self.assertEqual(len(folder["flagged"]), 1)
        flagged = folder["flagged"][0]
        self.assertEqual(flagged["file_path"], str(memory / "db.md"))
        self.assertEqual(flagged["findings"], [{"rule_id": "SECRET-011", "line": 3}])
        self.assertIn(FAKE_SECRET, flagged["content"])
        self.assertEqual(
            set(flagged), {"file_path", "file_name", "content", "size", "last_modified", "truncated", "findings"})
        self.assertNotIn("Prefer small diffs", json.dumps(memories))

    def test_a_secret_in_the_index_stays_in_index_with_its_findings(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "db is " + FAKE_SECRET + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["index"]["findings"], [{"rule_id": "SECRET-011", "line": 1}])
        self.assertEqual(folder["flagged"], [])
        self.assertEqual(folder["counts"], {"total": 1, "flagged": 1, "clean": 0, "truncated": 0})
        self.assertIsNone(folder["project_path"])

    def test_placeholder_credentials_are_excluded_like_the_backend_does(self):
        memory = _memory_dir(self.home)
        _write(memory / "defaults.md", "postgres://user:password@db.internal/shop\n")
        _write(memory / "local.md", "postgres://admin:Hunter2pass@localhost:5432/shop\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["counts"], {"total": 2, "flagged": 0, "clean": 2, "truncated": 0})
        self.assertIsNone(folder["index"])

    def test_two_matches_of_one_rule_on_one_line_are_one_finding(self):
        memory = _memory_dir(self.home)
        _write(memory / "db.md", "primary " + FAKE_SECRET + " replica " + FAKE_SECRET + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["flagged"][0]["findings"], [{"rule_id": "SECRET-011", "line": 1}])

    @unittest.skipUnless(os.name == "posix", "needs POSIX symlinks")
    def test_symlinks_and_non_markdown_files_are_skipped(self):
        memory = _memory_dir(self.home)
        _write(memory / "topic.md", "kept\n")
        _write(memory / "notes.txt", FAKE_SECRET)
        outside = _write(self._dir / "outside" / "secret.md", FAKE_SECRET)
        os.symlink(outside, memory / "linked.md")
        os.symlink(outside.parent, memory / "linked-dir")
        linked_project = self.home / ".claude" / "projects" / "-linked-project"
        linked_project.mkdir()
        os.symlink(outside.parent, linked_project / "memory")

        memories = self.collect()

        self.assertEqual(len(memories["folders"]), 1)
        self.assertEqual(memories["folders"][0]["counts"], {"total": 1, "flagged": 0, "clean": 1, "truncated": 0})
        self.assertNotIn("Hunter2pass", json.dumps(memories))

    def test_a_folder_owned_by_another_user_is_not_read(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "index\n")
        real_lstat = os.lstat

        def _foreign_owner(path, *args, **kwargs):
            real = real_lstat(path, *args, **kwargs)
            if Path(path) != memory:
                return real
            return os.stat_result(tuple(real[:4]) + (real.st_uid + 1,) + tuple(real[5:10]))

        with patch.object(cmh.os, "lstat", side_effect=_foreign_owner):
            self.assertEqual(self.collect()["folders"], [])
        self.assertEqual(len(self.collect()["folders"]), 1)

    def test_at_most_300_files_per_folder_and_the_index_survives_the_cap(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "index\n")
        for number in range(305):
            _write(memory / f"A{number:03d}.md", "topic\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["counts"]["total"], cmh.MAX_FILES_PER_MEMORY_FOLDER)
        self.assertEqual(folder["index"]["file_name"], "MEMORY.md")

    def test_the_walk_stops_at_the_depth_bound(self):
        memory = _memory_dir(self.home)
        deepest = memory.joinpath(*["level"] * MAX_SEARCH_DEPTH)
        _write(deepest / "kept.md", "at the bound\n")
        _write(deepest / "level" / "dropped.md", "past the bound\n")

        self.assertEqual(self.collect()["folders"][0]["counts"]["total"], 1)

    def test_an_index_over_the_size_cap_is_sent_truncated(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "x" * (MAX_CONFIG_FILE_SIZE + 500))

        index = self.collect()["folders"][0]["index"]

        self.assertTrue(index["truncated"])
        self.assertEqual(len(index["content"]), MAX_CONFIG_FILE_SIZE)
        self.assertEqual(index["size"], MAX_CONFIG_FILE_SIZE + 500)

    def test_a_topic_file_over_the_size_cap_is_counted_truncated_not_clean(self):
        memory = _memory_dir(self.home)
        _write(memory / "big.md", "x" * (MAX_CONFIG_FILE_SIZE + 1))
        _write(memory / "small.md", "ok\n")

        cold = self.collect()["folders"][0]
        warm = self.collect()["folders"][0]

        self.assertEqual(cold["counts"], {"total": 2, "flagged": 0, "clean": 1, "truncated": 1})
        self.assertEqual(cold["flagged"], [])
        self.assertEqual(warm["counts"], cold["counts"])

    def test_subagent_folders_carry_agent_name_and_scope(self):
        project = self._dir / "work" / "shop"
        _write(_memory_dir(self.home).parent / "session.jsonl", json.dumps({"cwd": str(project)}) + "\n")
        user_agent = self.home / ".claude" / "agent-memory" / "reviewer"
        project_agent = project / ".claude" / "agent-memory" / "planner"
        local_agent = project / ".claude" / "agent-memory-local" / "planner"
        for agent_dir in (user_agent, project_agent, local_agent):
            _write(agent_dir / "MEMORY.md", "agent notes\n")

        memories = self.collect()

        self.assertEqual(
            [(folder["path"], folder["project_path"], folder["memory_type"], folder["agent_name"], folder["scope"])
             for folder in memories["folders"]],
            [(str(user_agent), None, "subagent", "reviewer", "user"),
             (str(project_agent), str(project), "subagent", "planner", "project"),
             (str(local_agent), str(project), "subagent", "planner", "local")],
        )

    def test_a_deeply_nested_transcript_line_does_not_stop_the_scan(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "index\n")
        _write(memory.parent / "session.jsonl", "[" * 100000 + "\n" + json.dumps({"cwd": "/work/shop"}) + "\n")

        self.assertEqual(self.collect()["folders"][0]["project_path"], "/work/shop")

    def test_a_relocated_auto_memory_directory_is_scanned(self):
        relocated = self.home / "notes" / "claude-memory"
        _write(relocated / "MEMORY.md", "moved\n")
        _write(self.home / ".claude" / "settings.json", json.dumps({"autoMemoryDirectory": "~/notes/claude-memory"}))

        folder = _folder(self.collect(), relocated)

        self.assertEqual((folder["memory_type"], folder["scope"], folder["project_path"]), ("auto", "user", None))
        self.assertEqual(folder["index"]["content"], "moved\n")

    def test_a_relocated_directory_that_is_the_home_or_above_it_is_refused(self):
        _write(self.home / "README.md", FAKE_SECRET)
        for relocated in ("~/", str(self.home), str(self.home.parent)):
            _write(self.home / ".claude" / "settings.json", json.dumps({"autoMemoryDirectory": relocated}))
            self.assertEqual(self.collect()["folders"], [], relocated)

    def test_no_memory_files_still_gives_an_empty_block(self):
        _memory_dir(self.home).mkdir(parents=True)

        self.assertEqual(self.collect(), {"hash": hashlib.sha256(b"[]").hexdigest(), "folders": []})

    def test_the_hash_ignores_last_modified_and_follows_content(self):
        index = _write(_memory_dir(self.home) / "MEMORY.md", "index\n")
        first = self.collect()
        os.utime(index, (1_700_000_000, 1_700_000_000))
        touched = self.collect()
        _write(index, "index, edited\n")
        edited = self.collect()

        self.assertNotEqual(
            touched["folders"][0]["index"]["last_modified"], first["folders"][0]["index"]["last_modified"])
        self.assertEqual(touched["hash"], first["hash"])
        self.assertNotEqual(edited["hash"], first["hash"])

    def test_url_hosts_are_lowercased_deduplicated_and_need_a_dot(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "See https://Docs.Example.com/guide, and (https://api.example.com/v1).\n")
        _write(memory / "links.md",
               "http://localhost:3000/x http://intranet/wiki https://docs.example.com. "
               "https://grafana.internal.io:3000/d\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["url_hosts"], ["api.example.com", "docs.example.com", "grafana.internal.io"])


class TestMemoryCache(_MemoryHome):

    COUNTS = {"total": 3, "flagged": 1, "clean": 2, "truncated": 0}

    def setUp(self):
        super().setUp()
        self.memory = _memory_dir(self.home)
        _write(self.memory / "MEMORY.md", "index\n")
        _write(self.memory / "clean.md", "nothing here, see https://wiki.example.com\n")
        _write(self.memory / "db.md", "url: " + FAKE_SECRET + "\n")

    def test_an_unchanged_clean_file_is_not_read_again(self):
        cold, cold_reads = self.collect_recording_reads()
        warm, warm_reads = self.collect_recording_reads()

        self.assertIn("clean.md", cold_reads)
        self.assertNotIn("clean.md", warm_reads)
        self.assertEqual(warm, cold)
        self.assertIn(FAKE_SECRET, warm["folders"][0]["flagged"][0]["content"])
        self.assertEqual(warm["folders"][0]["url_hosts"], ["wiki.example.com"])

    def test_the_cache_holds_hashes_and_rule_ids_keyed_by_home_user_never_content(self):
        self.collect()

        cache_text = self.cache_file.read_text(encoding="utf-8")
        entry = json.loads(cache_text)["users"]["alice"][str(self.memory / "db.md")]
        self.assertEqual(set(entry), {"size", "mtime", "sha256", "findings", "url_hosts"})
        self.assertEqual(entry["findings"], [{"rule_id": "SECRET-011", "line": 1}])
        self.assertNotIn("Hunter2pass", cache_text)
        self.assertEqual(list(self.home.rglob(cmh.MEMORY_CACHE_FILENAME)), [])
        if os.name == "posix":
            self.assertEqual(self.cache_file.stat().st_mode & 0o777, 0o600)

        self.collect(home_user="bob")
        self.assertEqual(set(json.loads(self.cache_file.read_text())["users"]), {"alice", "bob"})

    def test_a_changed_file_is_rescanned(self):
        self.assertEqual(self.collect()["folders"][0]["counts"]["flagged"], 1)

        _write(self.memory / "clean.md", "now holds " + FAKE_SECRET + "\n")
        _write(self.memory / "db.md", "secret removed\n")
        folder = self.collect()["folders"][0]

        self.assertEqual([item["file_name"] for item in folder["flagged"]], ["clean.md"])
        self.assertEqual(folder["counts"], self.COUNTS)

    def test_a_rewrite_with_the_same_size_and_mtime_is_rescanned(self):
        db_file = self.memory / "db.md"
        self.assertEqual(self.collect()["folders"][0]["counts"]["flagged"], 1)
        before = db_file.stat()

        db_file.write_text("x" * before.st_size, encoding="utf-8")
        os.utime(db_file, ns=(before.st_atime_ns, before.st_mtime_ns))
        folder = self.collect()["folders"][0]

        self.assertEqual((db_file.stat().st_size, db_file.stat().st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual(folder["flagged"], [])
        self.assertEqual(folder["counts"]["flagged"], 0)

    def test_a_touched_file_with_the_same_content_keeps_its_verdict_without_a_rescan(self):
        cold = self.collect()
        os.utime(self.memory / "clean.md", (1_700_000_000, 1_700_000_000))

        with patch.object(cmh, "find_secrets", side_effect=AssertionError("rescanned")):
            touched, reads = self.collect_recording_reads()

        self.assertIn("clean.md", reads)
        self.assertEqual(touched["folders"][0]["counts"], cold["folders"][0]["counts"])

    def test_a_corrupt_cache_means_a_full_rescan_not_a_crash(self):
        cold = self.collect()
        entry_path = str(self.memory / "clean.md")
        file_stat = (self.memory / "clean.md").stat()
        matching = {"size": file_stat.st_size, "mtime": file_stat.st_mtime_ns}
        corrupt_caches = (
            "{not json",
            json.dumps(["a", "list"]),
            json.dumps({"rules_version": cmh.RULES_VERSION, "users": {"alice": "junk"}}),
            json.dumps({"rules_version": cmh.RULES_VERSION, "users": {"alice": {entry_path: matching}}}),
            json.dumps({"rules_version": cmh.RULES_VERSION, "users": {"alice": {entry_path: {
                **matching, "sha256": "0", "findings": [7], "url_hosts": [3]}}}}),
            json.dumps({"rules_version": cmh.RULES_VERSION, "users": {"alice": {entry_path: {
                **matching, "sha256": "0", "findings": {}, "url_hosts": "wiki"}}}}),
        )
        for corrupt in corrupt_caches:
            self.cache_file.write_text(corrupt)
            memories, reads = self.collect_recording_reads()
            self.assertEqual(memories, cold, corrupt)
            self.assertIn("clean.md", reads, corrupt)

    def test_a_rules_version_bump_rescans_everything(self):
        self.collect()

        with patch.object(cmh, "RULES_VERSION", cmh.RULES_VERSION + 1):
            _, reads = self.collect_recording_reads()

        self.assertIn("clean.md", reads)
        self.assertEqual(json.loads(self.cache_file.read_text())["rules_version"], cmh.RULES_VERSION + 1)

    def test_a_scan_out_of_time_sends_nothing_and_the_next_run_resumes(self):
        clock = _SimulatedClock()

        def _slow_scan(_content):
            clock.sleep(cmh.MEMORY_SCAN_BUDGET_SECONDS + 1)
            return []

        with patch.object(cmh, "time", clock), patch.object(cmh, "find_secrets", side_effect=_slow_scan):
            self.assertIsNone(self.collect())
        self.assertEqual(
            list(json.loads(self.cache_file.read_text())["users"]["alice"]), [str(self.memory / "MEMORY.md")])

        rescanned = []
        real_find_secrets = cmh.find_secrets
        with patch.object(cmh, "find_secrets",
                          side_effect=lambda content: rescanned.append(content) or real_find_secrets(content)):
            resumed = self.collect()

        self.assertEqual(len(rescanned), 2)
        self.assertNotIn("index\n", rescanned)
        self.assertEqual(resumed["folders"][0]["counts"], self.COUNTS)

    def test_running_out_of_time_keeps_the_verdicts_already_cached(self):
        self.collect()
        cached = json.loads(self.cache_file.read_text())

        with patch.object(cmh, "MEMORY_SCAN_BUDGET_SECONDS", 0):
            self.assertIsNone(self.collect())

        self.assertEqual(json.loads(self.cache_file.read_text()), cached)

    @unittest.skipUnless(os.name == "posix", "needs POSIX directory permissions")
    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0, "root writes through the directory mode")
    def test_an_unwritable_state_dir_still_reports_memories(self):
        self.addCleanup(os.chmod, self.state_dir, 0o700)
        os.chmod(self.state_dir, 0o500)

        self.assertEqual(self.collect()["folders"][0]["counts"]["total"], 3)


class _ReplyingBackend:
    """A localhost backend whose scan-start answer carries a chosen body; S3 is refused unless asked for."""

    def __init__(self, reply, report_status=200, s3=False):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _answer(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def _body(self):
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

            def do_PUT(self):
                outer._record(outer.uploaded, self._body())
                self._answer(200, "")

            def do_POST(self):
                body = self._body()
                if "upload-url" in self.path:
                    upload = {"upload_url": f"{outer.url}/s3/report.json", "object_key": "org/1/report.json"}
                    self._answer(200 if s3 else 500, json.dumps(upload))
                elif "from-s3" in self.path:
                    outer._record(outer.notified, body)
                    self._answer(202, "{}")
                else:
                    outer._record(outer.received, body)
                    self._answer(report_status if body.get("tools") else 200, reply)

            def log_message(self, *_args):
                pass

        self._lock = threading.Lock()
        self.received, self.uploaded, self.notified = [], [], []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _record(self, bodies, body):
        with self._lock:
            bodies.append(body)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def tool_reports(self):
        with self._lock:
            return [body for body in self.received if body.get("tools")]


def _report(tool, _device_id, home_user, _system_user=None, _run_id=None):
    return {"home_user": home_user, "device_id": "THIS-RUN", "system_user": None,
            "tools": [{key: value for key, value in tool.items() if not key.startswith("_")}]}


def _unserialisable_report(*args):
    return {**_report(*args), "not_json": {1, 2}}


class TestMemoriesOnTheToolReport(_DrivesMain, unittest.TestCase):
    """main() against a real localhost backend: the scan-start answer is the gate."""

    TOOLS = ({"name": "Claude Code", "version": "1.0", "install_path": "/usr/local/bin/claude"},)
    ENABLED = '{"ok": true, "memory_scan_enabled": true}'

    def setUp(self):
        super().setUp()
        self.home = Path(self._dir).resolve()
        self.memory = _memory_dir(self.home)
        _write(self.memory / "MEMORY.md", "index\n")
        _write(self.memory / "db.md", "url: " + FAKE_SECRET + "\n")

    def run_scan(self, reply, extra_patches=(), extra_args=(), fresh_report=_report, **backend_options):
        self.backend = _ReplyingBackend(reply, **backend_options)
        self.addCleanup(self.backend.close)
        argv = ["ai_tools_discovery.py", "--api-key", "test-key-000000", "--domain", self.backend.url,
                "--timeout", "12000"] + list(extra_args)
        self.run_main(
            tools=self.TOOLS, users=("alice",), fresh_report=fresh_report, backend=self.backend,
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=self.home),
                patch.object(adm, "find_claude_binary_for_user", return_value=None),
                patch.object(adm, "get_claude_subscription_type", return_value=None),
                patch.object(sys, "argv", argv),
            ) + tuple(extra_patches),
        )
        return self.backend.tool_reports()

    def test_no_memories_unless_the_scan_start_answer_says_exactly_true(self):
        for reply in ('{"ok": true}', '{"memory_scan_enabled": false}', '{"memory_scan_enabled": "true"}',
                      '{"memory_scan_enabled": 1}', "not json", "[true]"):
            adm.discovery_cache.forget_tool_hash("Claude Code", "alice")
            reports = self.run_scan(reply)
            self.assertEqual(len(reports), 1, reply)
            self.assertNotIn("memories", reports[0]["tools"][0], reply)
            self.assertNotIn("memories_hash", reports[0], reply)

    def test_memories_ride_on_the_claude_code_tool_when_the_answer_says_true(self):
        reports = self.run_scan(self.ENABLED)

        self.assertEqual(len(reports), 1)
        tool = reports[0]["tools"][0]
        self.assertEqual(tool["name"], "Claude Code")
        folder = tool["memories"]["folders"][0]
        self.assertEqual(folder["index"]["content"], "index\n")
        self.assertEqual(folder["flagged"][0]["findings"], [{"rule_id": "SECRET-011", "line": 1}])
        without_memories = {key: value for key, value in tool.items() if key != "memories"}
        self.assertEqual(reports[0]["payload_hash"], compute_payload_hash(without_memories))
        self.assertEqual(reports[0]["memories_hash"], tool["memories"]["hash"])

    def test_the_s3_notify_body_carries_memories_hash(self):
        self.run_scan(self.ENABLED, s3=True)

        self.assertEqual(len(self.backend.notified), 1)
        uploaded_memories = self.backend.uploaded[0]["tools"][0]["memories"]
        self.assertEqual(self.backend.notified[0]["memories_hash"], uploaded_memories["hash"])

    def test_an_empty_block_is_sent_when_there_are_no_memory_files(self):
        shutil.rmtree(self.memory)

        reports = self.run_scan(self.ENABLED)

        self.assertEqual(reports[0]["tools"][0]["memories"]["folders"], [])
        self.assertEqual(reports[0]["memories_hash"], hashlib.sha256(b"[]").hexdigest())

    def test_a_memory_only_change_uploads_again_under_the_same_tool_hash(self):
        first = self.run_scan(self.ENABLED)
        unchanged = self.run_scan(self.ENABLED)
        _write(self.memory / "MEMORY.md", "index, edited\n")
        edited = self.run_scan(self.ENABLED)

        self.assertEqual((len(first), len(unchanged), len(edited)), (1, 0, 1))
        self.assertEqual(edited[0]["payload_hash"], first[0]["payload_hash"])
        self.assertNotEqual(edited[0]["memories_hash"], first[0]["memories_hash"])

    def test_a_queued_report_is_stored_without_memories(self):
        reports = self.run_scan(self.ENABLED, report_status=500)

        self.assertIn(FAKE_SECRET, json.dumps(reports[0]["tools"][0]["memories"]))
        queued = json.loads(self.queue_file.read_text())
        self.assertEqual([envelope["report"]["tools"][0]["name"] for envelope in queued], ["Claude Code"])
        self.assertNotIn("memories", queued[0]["report"]["tools"][0])
        self.assertNotIn("Hunter2pass", self.queue_file.read_text())

    def test_a_failure_inside_memory_discovery_leaves_the_tool_report_intact(self):
        sentry = Mock()
        reports = self.run_scan(
            self.ENABLED,
            extra_patches=(patch.object(cmh, "_memory_folders", side_effect=RuntimeError("boom")),
                           patch.object(cmh, "report_to_sentry", sentry)),
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["tools"][0], {"name": "Claude Code", "version": "1.0",
                                                  "install_path": "/usr/local/bin/claude", "projects": []})
        self.assertEqual(sentry.call_args[0][1], {"phase": "memory_scan"})
        self.assertEqual(sentry.call_args[1], {"level": "warning"})

    def payload_log(self, **scan_options):
        root_logger = logging.getLogger()
        self.addCleanup(root_logger.setLevel, root_logger.level)
        with self.assertLogs(adm.payload_logger, level="INFO") as captured:
            self.run_scan(self.ENABLED, extra_args=("--payload",), **scan_options)
        return "\n".join(captured.output)

    def test_the_payload_log_masks_memory_content(self):
        logged = self.payload_log()

        self.assertIn(FAKE_SECRET, json.dumps(self.backend.tool_reports()[0]["tools"][0]["memories"]))
        self.assertIn('"memories"', logged)
        self.assertNotIn("Hunter2pass", logged)

    def test_the_payload_log_fallback_prints_keys_not_memory_content(self):
        logged = self.payload_log(fresh_report=_unserialisable_report)

        self.assertIn("not_json", logged)
        self.assertNotIn("Hunter2pass", logged)


if __name__ == "__main__":
    unittest.main()
