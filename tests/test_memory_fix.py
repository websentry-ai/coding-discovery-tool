"""Memory file Fix It: the helper that edits the file, the poll that asks the backend, and the scan's hook points."""

import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

if os.name == "nt":
    raise unittest.SkipTest("Fix It runs on macOS only; its helper needs POSIX file calls")

import pwd  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.ai_tools_discovery as adm
import scripts.coding_discovery_tools.memory_fix as memory_fix
import scripts.coding_discovery_tools.memory_fix_helper as helper
from scripts.coding_discovery_tools.memory_secret_rules import find_secrets
from tests.test_queue_drain_budget import _DrivesMain, _SimulatedClock

MARKER = "[credential removed by Unbound]"
SECRET_A = "ghp_" + "a1B2c3D4" * 4 + "e5F6"
SECRET_B = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
ME = pwd.getpwuid(os.getuid()).pw_name
# Copied from ai-gateway-data webapp/services/memory_ingest_service.py.
CREDENTIAL_MASK = "[REDACTED-CREDENTIAL]"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _span(text: str, part: str) -> list:
    start = text.index(part)
    return [start, start + len(part)]


class _Folder(unittest.TestCase):
    """A memory folder on disk, under a real path with no symlink in it."""

    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="memory-fix-")))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "memory"
        self.root.mkdir()

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def fix(self, path: Path, data: bytes, spans: list, sha: str = None, root: Path = None) -> dict:
        return helper.fix_file(str(path), str(root or self.root), sha or _sha(data), spans)

    def assert_only(self, *names: str, folder: Path = None):
        self.assertEqual(sorted(os.listdir(folder or self.root)), sorted(names))


class HelperEditsTheFile(_Folder):

    def test_an_exact_match_is_fixed_and_only_the_value_changes(self):
        text = "token: " + SECRET_A + "\n"
        path = self.write("MEMORY.md", text.encode())

        self.assertEqual(self.fix(path, text.encode(), [_span(text, SECRET_A)]), {"outcome": "fixed"})

        self.assertEqual(path.read_bytes(), ("token: " + MARKER + "\n").encode())
        self.assert_only("MEMORY.md")

    def test_two_spans_are_cut_back_to_front(self):
        text = "a " + SECRET_A + " b " + SECRET_B + " c"
        path = self.write("db.md", text.encode())

        self.fix(path, text.encode(), [_span(text, SECRET_A), _span(text, SECRET_B)])

        self.assertEqual(path.read_text(), "a " + MARKER + " b " + MARKER + " c")

    def test_crlf_emoji_and_bom_are_kept_byte_for_byte(self):
        text = "﻿# note \U0001F511 café\r\nkey: " + SECRET_A + "\r\n中文 " + SECRET_B + "\r\n"
        path = self.write("x.md", text.encode("utf-8"))

        self.fix(path, text.encode("utf-8"), [_span(text, SECRET_A), _span(text, SECRET_B)])

        expected = text.replace(SECRET_A, MARKER).replace(SECRET_B, MARKER).encode("utf-8")
        self.assertEqual(path.read_bytes(), expected)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_the_file_mode_is_kept(self):
        text = "t " + SECRET_A
        path = self.write("m.md", text.encode())
        os.chmod(path, 0o640)

        self.fix(path, text.encode(), [_span(text, SECRET_A)])

        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)


    @unittest.skipUnless(sys.platform == "darwin", "ACLs and extended attributes are copied on macOS only")
    def test_the_acl_and_extended_attributes_are_kept(self):
        import subprocess
        text = "token: " + SECRET_A + "\n"
        path = self.write("MEMORY.md", text.encode())
        subprocess.run(["/usr/bin/xattr", "-w", "com.unbound.test", "kept", str(path)], check=True)
        subprocess.run(["/bin/chmod", "+a", "group:staff allow read", str(path)], check=True)

        self.assertEqual(self.fix(path, text.encode(), [_span(text, SECRET_A)]), {"outcome": "fixed"})

        kept = subprocess.run(["/usr/bin/xattr", "-p", "com.unbound.test", str(path)], capture_output=True, text=True)
        listing = subprocess.run(["/bin/ls", "-le", str(path)], capture_output=True, text=True).stdout
        self.assertEqual((kept.stdout.strip(), "group:staff allow read" in listing), ("kept", True))
        self.assertEqual(path.read_bytes(), ("token: " + MARKER + "\n").encode())


class HelperRefusesAndLeavesTheFileAlone(_Folder):

    def assert_failed(self, answer: dict, reason: str):
        self.assertEqual(answer, {"outcome": "failed", "reason": reason})

    def test_a_wrong_hash_is_changed_and_nothing_is_written(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)

        self.assert_failed(self.fix(path, text, [[2, len(text)]], sha="0" * 64), "changed")

        self.assertEqual(path.read_bytes(), text)
        self.assert_only("m.md")

    def test_a_file_changed_between_read_and_rename_is_changed_and_no_temp_is_left(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        real = helper._still_same

        def edited_meanwhile(dir_fd, name, before):
            with open(path, "ab") as handle:
                handle.write(b" more")
            return real(dir_fd, name, before)

        with patch.object(helper, "_still_same", edited_meanwhile):
            self.assert_failed(self.fix(path, text, [[2, len(text)]]), "changed")

        self.assertEqual(path.read_bytes(), text + b" more")
        self.assert_only("m.md")

    @unittest.skipUnless(sys.platform == "darwin", "the atomic swap is macOS only")
    def test_a_save_landing_in_the_instant_of_the_swap_is_kept_and_no_temp_is_left(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        real = helper._exchange
        saves = []

        def saved_just_before(dir_fd, first, second):
            if not saves:
                saves.append(1)
                newer = path.with_name("newer")
                newer.write_bytes(text + b" and a line the user just added")
                os.replace(newer, path)
            return real(dir_fd, first, second)

        with patch.object(helper, "_exchange", saved_just_before):
            self.assert_failed(self.fix(path, text, [[2, len(text)]]), "changed")

        self.assertEqual(path.read_bytes(), text + b" and a line the user just added")
        self.assert_only("m.md")

    @unittest.skipUnless(sys.platform == "darwin", "the atomic swap is macOS only")
    def test_a_second_save_landing_during_the_swap_back_is_the_one_kept(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        real = helper._exchange
        saves = []

        def saved_just_before(dir_fd, first, second):
            if len(saves) < 2:
                saves.append(1)
                newer = path.with_name("newer")
                newer.write_bytes(text + b" save %d" % len(saves))
                os.replace(newer, path)
            return real(dir_fd, first, second)

        with patch.object(helper, "_exchange", saved_just_before):
            self.assert_failed(self.fix(path, text, [[2, len(text)]]), "changed")

        self.assertEqual(path.read_bytes(), text + b" save 2")
        self.assert_only("m.md")

    def test_a_swap_the_os_refuses_changes_nothing_and_a_missing_swap_falls_back_to_the_rename(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)

        with patch.object(helper, "_exchange", return_value=False):
            self.assert_failed(self.fix(path, text, [[2, len(text)]]), "changed")
        self.assertEqual(path.read_bytes(), text)
        self.assert_only("m.md")

        with patch.object(helper, "_exchange", return_value=None):
            self.assertEqual(self.fix(path, text, [[2, len(text)]]), {"outcome": "fixed"})
        self.assertEqual(path.read_bytes(), b"t " + MARKER.encode())
        self.assert_only("m.md")

    def test_a_symlinked_file_is_refused(self):
        outside = self.base / "outside.md"
        outside.write_bytes(b"t " + SECRET_A.encode())
        (self.root / "m.md").symlink_to(outside)

        self.assert_failed(self.fix(self.root / "m.md", outside.read_bytes(), [[2, 10]]), "refused")

        self.assertEqual(outside.read_bytes(), b"t " + SECRET_A.encode())

    def test_a_symlinked_parent_folder_is_refused(self):
        real_dir = self.base / "elsewhere"
        real_dir.mkdir()
        (real_dir / "m.md").write_bytes(b"t " + SECRET_A.encode())
        (self.root / "sub").symlink_to(real_dir)

        self.assert_failed(self.fix(self.root / "sub" / "m.md", (real_dir / "m.md").read_bytes(), [[2, 10]]),
                           "refused")

        self.assertEqual((real_dir / "m.md").read_bytes(), b"t " + SECRET_A.encode())
        self.assert_only("m.md", folder=real_dir)

    def test_a_symlink_above_the_memory_root_is_refused(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root)
        text = b"t " + SECRET_A.encode()
        self.write("m.md", text)

        self.assert_failed(self.fix(alias / "m.md", text, [[2, 10]], root=alias), "refused")

        self.assertEqual((self.root / "m.md").read_bytes(), text)

    def test_a_hard_link_is_refused(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        os.link(path, self.base / "other.md")

        self.assert_failed(self.fix(path, text, [[2, 10]]), "refused")

        self.assertEqual(path.read_bytes(), text)

    def test_a_path_outside_the_memory_root_is_refused(self):
        text = b"t " + SECRET_A.encode()
        (self.base / "x.md").write_bytes(text)

        for path in (str(self.root / ".." / "x.md"), "memory/x.md", str(self.base / "x.md")):
            self.assert_failed(helper.fix_file(path, str(self.root), _sha(text), [[2, 10]]), "refused")
        self.assert_failed(helper.fix_file(str(self.root / "x.md"), "memory", _sha(text), [[2, 10]]), "refused")

        self.assertEqual((self.base / "x.md").read_bytes(), text)

    def test_a_file_over_50_kb_is_too_large(self):
        data = b"a" * (helper.MAX_FILE_BYTES + 1)
        path = self.write("big.md", data)

        self.assert_failed(self.fix(path, data, [[0, 5]]), "too_large")

        self.assertEqual(path.read_bytes(), data)

    def test_a_fix_that_would_grow_the_file_past_50_kb_is_too_large_and_nothing_is_written(self):
        text = "x" * (helper.MAX_FILE_BYTES - 10 - 1) + "\n" + "s" * 10
        path = self.write("edge.md", text.encode())

        self.assertEqual(self.fix(path, text.encode(), [_span(text, "s" * 10)]),
                         {"outcome": "failed", "reason": "too_large"})

        self.assertEqual(path.read_bytes(), text.encode())
        self.assert_only("edge.md")

    def test_invalid_utf8_is_not_utf8(self):
        data = b"t \xff\xfe " + SECRET_A.encode()
        path = self.write("bin.md", data)

        self.assert_failed(self.fix(path, data, [[5, 10]]), "not_utf8")

        self.assertEqual(path.read_bytes(), data)

    def test_a_missing_file_or_folder_is_missing(self):
        self.assert_failed(self.fix(self.root / "gone.md", b"x", [[0, 1]]), "missing")
        self.assert_failed(self.fix(self.root / "no" / "gone.md", b"x", [[0, 1]]), "missing")

    def test_bad_spans_are_refused(self):
        text = "a " + SECRET_A + " b " + SECRET_B
        path = self.write("m.md", text.encode())
        a, b = _span(text, SECRET_A), _span(text, SECRET_B)

        for spans in ([], [b, a], [[a[0], b[0] + 1], b], [[0, len(text) + 1]], [[-1, 3]], [[3, 3]],
                      [[True, 3]], [[1.0, 3]], [[1, 2, 3]], ["0-3"], [{"start": 0, "end": 3}]):
            self.assert_failed(self.fix(path, text.encode(), spans), "refused")

        self.assertEqual(path.read_text(), text)
        self.assert_only("m.md")

    def test_a_write_refused_by_the_folder_leaves_the_file(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        os.chmod(self.root, 0o500)
        self.addCleanup(os.chmod, self.root, 0o700)

        self.assert_failed(self.fix(path, text, [[2, 10]]), "refused")

        self.assertEqual(path.read_bytes(), text)
        self.assert_only("m.md")


class HelperProtocol(_Folder):

    def run_main(self, raw: bytes):
        stdin, stdout = io.TextIOWrapper(io.BytesIO(raw)), io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            code = helper.main()
        return code, stdout.getvalue()

    def test_ping(self):
        self.assertEqual(self.run_main(b'{"op":"ping"}'),
                         (0, '{"ok": true, "helper": "memory-fix", "protocol": 1}\n'))

    def test_a_fix_answers_one_json_line(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        request = {"op": "fix", "file_path": str(path), "memory_root": str(self.root),
                   "sha256": _sha(text), "spans": [[2, len(text)]]}

        self.assertEqual(self.run_main(json.dumps(request).encode()), (0, '{"outcome": "fixed"}\n'))
        self.assertEqual(path.read_bytes(), b"t " + MARKER.encode())

    def test_a_malformed_request_exits_2_and_says_nothing(self):
        good = {"op": "fix", "file_path": "/a/b", "memory_root": "/a", "sha256": "0" * 64, "spans": []}
        for raw in (b"", b"not json", b"[1]", b'{"op": "nope"}', b"\xff",
                    json.dumps({**good, "sha256": "abc"}).encode(), json.dumps({**good, "spans": "0-1"}).encode(),
                    json.dumps({**good, "file_path": 3}).encode(), json.dumps({**good, "memory_root": None}).encode(),
                    json.dumps({**good, "file_path": "/a/\u0000b"}).encode()):
            self.assertEqual(self.run_main(raw), (2, ""), raw)

    def test_the_marker_cannot_come_from_the_request(self):
        text = b"t " + SECRET_A.encode()
        path = self.write("m.md", text)
        request = {"op": "fix", "file_path": str(path), "memory_root": str(self.root), "sha256": _sha(text),
                   "spans": [[2, len(text)]], "marker": "evil", "replacement": "evil"}

        self.run_main(json.dumps(request).encode())

        self.assertEqual(path.read_bytes(), b"t " + MARKER.encode())

    def test_the_real_launch_answers_a_ping_with_an_empty_environment(self):
        self.assertEqual(memory_fix.run_helper({"op": "ping"}, None, None),
                         {"ok": True, "helper": "memory-fix", "protocol": 1})


def _backend_store(text: str, secret_spans: list) -> tuple:
    """(pieces, secrets, value spans per mask) as the backend stores a file; secret_spans are (start, end, value)."""
    pieces, secrets, cursor = [], [], 0
    for start, end, _ in secret_spans:
        pieces.append(text[cursor:start])
        secrets.append(text[start:end])
        cursor = end
    pieces.append(text[cursor:])
    value_spans = [[[value[0] - start, value[1] - start]] for start, _, value in secret_spans]
    return CREDENTIAL_MASK.join(pieces).split(CREDENTIAL_MASK), secrets, value_spans


def _backend_hash_and_positions(pieces: list, secrets: list, value_spans: list) -> tuple:
    """Copied from ai-gateway-data webapp/services/memory_fix_service.py _text_hash and _positions."""
    digest = hashlib.sha256(
        ("".join(piece + secret for piece, secret in zip(pieces, secrets)) + pieces[-1]).encode("utf-8")).hexdigest()
    starts, cursor = [], 0
    for piece, secret in zip(pieces, secrets):
        starts.append(cursor + len(piece))
        cursor += len(piece) + len(secret)
    placed = {(starts[index] + start, starts[index] + end)
              for index, spans in enumerate(value_spans) for start, end in spans}
    return digest, [list(span) for span in sorted(placed)]


class GoldenMatchesTheBackend(_Folder):
    """The helper, given the backend's hash and positions, cuts exactly the values out of the real file."""

    def test_lf_crlf_emoji_and_bom(self):
        for newline, head in (("\n", ""), ("\r\n", ""), ("\n", "\U0001F511 café "), ("\r\n", "﻿")):
            text = (head + "# notes" + newline + "github token: " + SECRET_A + newline
                    + "中 stripe = '" + SECRET_B + "'" + newline)
            a_value, b_value = _span(text, SECRET_A), _span(text, SECRET_B)
            # The masked span holds the key too; only the value inside it is a position.
            secret_spans = [(a_value[0] - 7, a_value[1], a_value), (b_value[0] - 1, b_value[1] + 1, b_value)]
            digest, positions = _backend_hash_and_positions(*_backend_store(text, secret_spans))
            path = self.write("golden.md", text.encode("utf-8"))

            self.assertEqual(digest, _sha(path.read_bytes()))
            self.assertEqual(helper.fix_file(str(path), str(self.root), digest, positions), {"outcome": "fixed"})

            self.assertEqual(path.read_bytes(), text.replace(SECRET_A, MARKER).replace(SECRET_B, MARKER).encode())


class MarkerIsNotASecret(unittest.TestCase):

    def test_the_memory_rules_do_not_flag_the_marker_where_a_value_was(self):
        for line in ('password = "{}"', "password: {}", "DB_PASSWORD={}", "export API_TOKEN={}",
                     "Authorization: Bearer {}", '{{"api_key": "{}"}}', "token: {}", "apiKey: {}",
                     "aws_secret_access_key={}", "github token: {}"):
            self.assertEqual(find_secrets(line.format(MARKER)), [], line)


class _Files(_Folder):
    """Real memory files the poll hashes, asked about over real curl to a localhost backend; the helper is faked."""

    def setUp(self):
        super().setUp()
        self.clock = _SimulatedClock()
        clock_patch = patch.object(memory_fix, "time", self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.posts, self.helper_calls = [], []
        self.answers, self.fix_statuses, self.result_statuses = [], [], []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.posts.append((self.path, body))
                if self.path == memory_fix.RESULTS_PATH:
                    status = outer.result_statuses.pop(0) if outer.result_statuses else 200
                    reply = {"recorded": len(body.get("results", []))}
                else:
                    status = outer.fix_statuses.pop(0) if outer.fix_statuses else 200
                    reply = outer.answers.pop(0)(body) if outer.answers else {"fixes": []}
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def candidate(self, name: str, text: str) -> dict:
        path = self.write(name, text.encode("utf-8"))
        return {"home_user": ME, "home": str(self.base), "file_path": str(path), "memory_root": str(self.root)}

    def fake_helper(self, request, uid, gid, timeout=None):
        self.helper_calls.append((request, uid, gid))
        return helper.fix_file(request["file_path"], request["memory_root"], request["sha256"], request["spans"])

    def run_fixes(self, candidates, helper_fn=None, deadline=None):
        with patch.object(memory_fix, "run_helper", helper_fn or self.fake_helper):
            return memory_fix.apply_pending_fixes(self.url, "key", "dev-1", candidates, deadline=deadline)

    def fix_posts(self):
        return [body for path, body in self.posts if path == memory_fix.FIXES_PATH]

    def result_posts(self):
        return [body for path, body in self.posts if path == memory_fix.RESULTS_PATH]


def _ready(fix_id, candidate, spans, token="tok"):
    return {"id": fix_id, "home_user": candidate["home_user"], "file_path": candidate["file_path"],
            "status": "ready", "token": token, "spans": [{"start": s, "end": e} for s, e in spans]}


def _status(fix_id, candidate, status):
    return {"id": fix_id, "home_user": candidate["home_user"], "file_path": candidate["file_path"],
            "status": status}


class PollAsksTheBackend(_Files):

    def test_a_ready_fix_runs_the_helper_and_posts_the_result_with_its_token(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, SECRET_A)], token="T-7")]}]

        counts = self.run_fixes([held])

        posted = self.fix_posts()[0]
        self.assertEqual(posted, {"device_discovery_id": "dev-1", "files": [
            {"home_user": ME, "file_path": held["file_path"], "sha256": _sha(text.encode())}]})
        self.assertEqual(self.helper_calls[0][0], {
            "op": "fix", "file_path": held["file_path"], "memory_root": str(self.root),
            "sha256": _sha(text.encode()), "spans": [_span(text, SECRET_A)]})
        self.assertEqual(self.helper_calls[0][1:], (pwd.getpwnam(ME).pw_uid, pwd.getpwnam(ME).pw_gid))
        self.assertEqual(self.result_posts(), [{"device_discovery_id": "dev-1", "results": [
            {"id": 7, "outcome": "fixed", "token": "T-7"}]}])
        self.assertEqual(Path(held["file_path"]).read_text(), "t " + MARKER)
        self.assertEqual(counts, {"rounds": 1, "fixed": 1})

    def test_a_failed_helper_outcome_is_posted_with_its_reason(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [[0, len(text) + 5]])]}]

        self.run_fixes([held])

        self.assertEqual(self.result_posts()[0]["results"],
                         [{"id": 7, "outcome": "failed", "reason": "refused", "token": "tok"}])

    def test_changed_and_ready_files_are_never_posted_again(self):
        held = [self.candidate(f"{name}.md", f"{name} {SECRET_A}") for name in ("a", "b", "c")]
        self.answers = [lambda body: {"fixes": [_status(1, held[0], "changed"),
                                                _ready(2, held[1], [[2, 10]])]}]

        self.run_fixes(held)

        files_per_round = [[f["file_path"] for f in body["files"]] for body in self.fix_posts()]
        self.assertEqual(files_per_round, [[h["file_path"] for h in held]])

    def test_only_a_pending_file_is_asked_again_every_five_minutes_five_times_in_all(self):
        held = [self.candidate(f"{name}.md", f"{name} {SECRET_A}") for name in ("p", "absent")]
        self.answers = [lambda body: {"fixes": [_status(1, held[0], "pending")]}] * 100

        counts = self.run_fixes(held)

        self.assertEqual([len(body["files"]) for body in self.fix_posts()], [2] + [1] * 4)
        self.assertEqual(self.clock.slept, 4 * 300)
        self.assertEqual(counts, {"rounds": 5})
        self.assertEqual(self.result_posts(), [])

    def test_the_run_deadline_cuts_the_wait_short(self):
        held = self.candidate("p.md", "p " + SECRET_A)
        self.answers = [lambda body: {"fixes": [_status(1, held, "pending")]}] * 10

        self.run_fixes([held], deadline=self.clock.monotonic() + 700)

        self.assertEqual(len(self.fix_posts()), 3)  # at 0, 300 and 600 s; 900 s is past it

    def test_a_pending_file_that_turns_ready_later_is_fixed_then(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_status(4, held, "pending")]},
                        lambda body: {"fixes": [_status(4, held, "pending")]},
                        lambda body: {"fixes": [_ready(4, held, [_span(text, SECRET_A)])]}]

        counts = self.run_fixes([held])

        self.assertEqual(len(self.fix_posts()), 3)
        self.assertEqual(counts["fixed"], 1)

    def test_a_span_outside_what_the_agents_own_rules_flag_is_refused_without_touching_the_file(self):
        text = "never run rm -rf without asking\ntoken " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, "without asking")])]}]

        self.run_fixes([held])

        self.assertEqual(self.result_posts()[0]["results"],
                         [{"id": 7, "outcome": "failed", "reason": "refused", "token": "tok"}])
        self.assertEqual((self.helper_calls, Path(held["file_path"]).read_text()), ([], text))

    def test_a_fix_is_not_started_without_time_left_to_report_it(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, SECRET_A)])]}]

        counts = self.run_fixes([held], deadline=self.clock.monotonic() + 20)

        self.assertEqual((self.helper_calls, self.result_posts(), counts["no_answer"]), ([], [], 1))
        self.assertEqual(Path(held["file_path"]).read_text(), text)

    def test_a_result_post_that_fails_is_tried_again(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, SECRET_A)])]}]
        self.result_statuses = [500, 500]

        counts = self.run_fixes([held])

        self.assertEqual(len(self.result_posts()), 3)
        self.assertNotIn("results_post_failed", counts)

    def test_a_result_that_never_gets_through_is_counted(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, SECRET_A)])]}]
        self.result_statuses = [500, 500, 500]

        counts = self.run_fixes([held])

        self.assertEqual((len(self.result_posts()), counts["results_post_failed"]), (3, 1))

    def test_a_bad_answer_does_not_lose_the_result_of_a_fix_already_made(self):
        text = "t " + SECRET_A
        held = self.candidate("m.md", text)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [_span(text, SECRET_A)]),
                                                {"home_user": [], "file_path": {}, "status": "ready"}]}]

        with patch.object(memory_fix, "_spans", side_effect=[[_span(text, SECRET_A)], RuntimeError("boom")]):
            counts = self.run_fixes([held, self.candidate("n.md", "n " + SECRET_A)])

        self.assertEqual(self.result_posts()[0]["results"], [{"id": 7, "outcome": "fixed", "token": "tok"}])
        self.assertEqual(counts["fixed"], 1)

    def test_a_helper_that_answers_nothing_posts_no_result(self):
        held = self.candidate("m.md", "t " + SECRET_A)
        self.answers = [lambda body: {"fixes": [_ready(7, held, [[2, 10]])]}]

        counts = self.run_fixes([held], helper_fn=lambda *a, **k: None)

        self.assertEqual(self.result_posts(), [])
        self.assertEqual(counts["no_answer"], 1)
        self.assertEqual(Path(held["file_path"]).read_text(), "t " + SECRET_A)

    def test_files_that_are_not_whole_utf8_are_never_posted(self):
        bad = self.candidate("ok.md", "fine")
        Path(bad["file_path"]).write_bytes(b"\xff\xfe broken")
        big = self.candidate("big.md", "a" * (helper.MAX_FILE_BYTES + 1))
        good = self.candidate("good.md", "t " + SECRET_A)

        counts = self.run_fixes([bad, big, good])

        self.assertTrue(all([f["file_path"] for f in body["files"]] == [good["file_path"]]
                            for body in self.fix_posts()))
        self.assertEqual(counts["not_posted"], 2)

    def test_garbage_answers_and_failed_posts_never_raise(self):
        held = self.candidate("m.md", "t " + SECRET_A)
        for answer in (None, {"fixes": "x"}, {"fixes": [1, None, {"status": "ready"}]},
                       {"fixes": [{**_ready("7", held, [[2, 10]])}]},
                       {"fixes": [{**_ready(7, held, [[2, 10]]), "spans": [{"start": "2", "end": 10}]}]},
                       {"fixes": [{**_ready(7, held, [[2, 10]]), "token": None}]},
                       {"fixes": [_status(7, held, "weird")]}):
            self.posts, self.answers = [], [lambda body, answer=answer: answer]
            self.run_fixes([held])
        self.assertEqual(Path(held["file_path"]).read_text(), "t " + SECRET_A)

    def test_the_post_itself_survives_a_dead_backend_an_error_status_and_bad_bodies(self):
        link = memory_fix._Link(self.url, "key", "dev", None)
        self.assertIsNone(memory_fix._post_json(memory_fix._Link("http://127.0.0.1:1", "key", "dev", None),
                                                memory_fix.FIXES_PATH, {}))
        self.fix_statuses = [500]
        self.assertIsNone(memory_fix._post_json(link, memory_fix.FIXES_PATH, {}))
        for reply in ("not an object", [1], None):
            self.answers = [lambda body, reply=reply: reply]
            self.assertIsNone(memory_fix._post_json(link, memory_fix.FIXES_PATH, {}), reply)
        self.assertEqual(memory_fix._post_json(link, memory_fix.FIXES_PATH, {}), {"fixes": []})

    def test_apply_pending_fixes_never_raises(self):
        with patch.object(memory_fix, "_poll", Mock(side_effect=RuntimeError("boom"))):
            self.assertEqual(memory_fix.apply_pending_fixes("http://x", "k", "d", []), {})


class HeldFiles(unittest.TestCase):

    def test_only_user_scope_files_under_the_users_claude_folder_are_held(self):
        home = Path("/Users/ann")
        auto = str(home / ".claude" / "projects" / "-w" / "memory")
        memories = {"folders": [
            {"path": auto, "scope": "user", "index": {"file_path": auto + "/MEMORY.md"},
             "flagged": [{"file_path": auto + "/db.md"}, {"file_path": auto + "/big.md", "truncated": True}]},
            {"path": "/Users/ann/notes/memory", "scope": "user", "index": {"file_path": "/Users/ann/notes/memory/a.md"},
             "flagged": []},
            {"path": "/w/.claude/agent-memory/x", "scope": "project", "index": None,
             "flagged": [{"file_path": "/w/.claude/agent-memory/x/a.md"}]},
        ]}

        self.assertEqual(memory_fix.held_files("ann", home, memories), [
            {"home_user": "ann", "home": "/Users/ann", "file_path": auto + "/MEMORY.md", "memory_root": auto},
            {"home_user": "ann", "home": "/Users/ann", "file_path": auto + "/db.md", "memory_root": auto},
        ])
        self.assertEqual(memory_fix.held_files("ann", home, None), [])


class SupportsFixes(unittest.TestCase):

    def gate(self, platform="darwin", euid=0, ping=None, users=(ME,)):
        ping = {"ok": True, "helper": "memory-fix", "protocol": 1} if ping is None else ping
        run = Mock(return_value=ping)
        with patch.object(memory_fix.sys, "platform", platform), patch.object(memory_fix, "_euid", return_value=euid), \
                patch.object(memory_fix, "run_helper", run), \
                patch.object(memory_fix, "_owner", side_effect=lambda name: {ME: (501, 20), "root": (0, 0)}.get(name)):
            return memory_fix.supports_fixes(list(users)), run

    def test_true_only_on_a_macos_root_run_whose_helper_answers(self):
        supported, run = self.gate(users=("root", "ghost", ME))
        self.assertTrue(supported)
        run.assert_called_once_with({"op": "ping"}, 501, 20, timeout=memory_fix.PING_TIMEOUT_SECONDS)

    def test_false_otherwise(self):
        for options in ({"platform": "linux"}, {"platform": "win32"}, {"euid": 501}, {"ping": {}},
                        {"ping": {"ok": True, "protocol": 2}}, {"ping": {"ok": "yes", "protocol": 1}},
                        {"users": ()}, {"users": ("root", "ghost")}):
            self.assertFalse(self.gate(**options)[0], options)
        with patch.object(memory_fix.sys, "platform", "darwin"), patch.object(memory_fix, "_euid", return_value=0), \
                patch.object(memory_fix, "_owner", Mock(side_effect=RuntimeError)):
            self.assertFalse(memory_fix.supports_fixes([ME]))

    def test_a_ping_that_fails_to_launch_is_false(self):
        with patch.object(memory_fix.sys, "platform", "darwin"), patch.object(memory_fix, "_euid", return_value=0), \
                patch.object(memory_fix, "helper_command", return_value=["/nonexistent/unbound-discovery"]), \
                patch.object(memory_fix, "_owner", return_value=(501, 20)), \
                patch.object(memory_fix.subprocess, "Popen", Mock(side_effect=PermissionError)):
            self.assertFalse(memory_fix.supports_fixes([ME]))

    def test_the_helper_runs_as_the_user_when_the_agent_is_root(self):
        child = Mock(returncode=0)
        child.communicate.return_value = (b'{"ok": true, "protocol": 1}\n', b"")
        with patch.object(memory_fix, "_euid", return_value=0), \
                patch.object(memory_fix.subprocess, "Popen", return_value=child) as popen:
            self.assertEqual(memory_fix.run_helper({"op": "ping"}, 501, 20), {"ok": True, "protocol": 1})
            self.assertIsNone(memory_fix.run_helper({"op": "ping"}, 0, 0))
        kwargs = popen.call_args[1]
        self.assertEqual((kwargs["user"], kwargs["group"], kwargs["extra_groups"], kwargs["env"], kwargs["cwd"]),
                         (501, 20, [], {}, "/"))
        self.assertEqual(popen.call_count, 1)

    def test_a_helper_reply_that_is_not_exactly_one_json_line_is_none(self):
        for out, code in ((b'{"ok": true}\n{"ok": true}\n', 0), (b'{"ok": true}', 0), (b"[1]\n", 0),
                          (b"junk\n", 0), (b'{"ok": true}\n', 3)):
            child = Mock(returncode=code)
            child.communicate.return_value = (out, b"")
            with patch.object(memory_fix, "_euid", return_value=501), \
                    patch.object(memory_fix.subprocess, "Popen", return_value=child):
                self.assertIsNone(memory_fix.run_helper({"op": "ping"}, None, None), out)

    def test_a_hung_helper_is_killed(self):
        child = Mock(returncode=None)
        child.communicate.side_effect = [memory_fix.subprocess.TimeoutExpired("x", 1), (b"", b"")]
        with patch.object(memory_fix, "_euid", return_value=501), \
                patch.object(memory_fix.subprocess, "Popen", return_value=child):
            self.assertIsNone(memory_fix.run_helper({"op": "ping"}, None, None, timeout=1))
        child.kill.assert_called_once()

    def test_frozen_and_source_commands(self):
        with patch.object(memory_fix.sys, "frozen", True, create=True), \
                patch.object(memory_fix.sys, "executable", "/opt/unbound/current/unbound-discovery/unbound-discovery"):
            self.assertEqual(memory_fix.helper_command()[1:], ["memory-fix"])
        command = memory_fix.helper_command()
        self.assertEqual(command[:2], [sys.executable, "-I"])
        self.assertEqual(command[2], os.path.abspath(helper.__file__))


class _FixBackend:
    """A localhost backend: scan start says fixes wait; the fixes endpoint places the db.md secret when its hash holds."""

    def __init__(self, text: str, pending: bool = True):
        outer = self
        self.received, self.lock = [], threading.Lock()

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                with outer.lock:
                    outer.received.append((self.path, body))
                if self.path.endswith("/memory-fixes/"):
                    reply = {"fixes": [_ready(9, held, [_span(text, SECRET_A)], token="T-9")
                                       for held in body["files"] if held["file_path"].endswith("db.md")
                                       and held["sha256"] == _sha(text.encode())]}
                elif self.path.endswith("/memory-fixes/results/"):
                    reply = {"recorded": len(body["results"])}
                elif "upload-url" in self.path:
                    reply = {}
                else:
                    reply = {"ok": True, "memory_scan_enabled": True, "memory_fixes_pending": pending}
                data = json.dumps(reply).encode()
                self.send_response(500 if "upload-url" in self.path else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self, suffix: str) -> list:
        with self.lock:
            return [body for path, body in self.received if path.endswith(suffix)]

    def scan_events(self, event: str) -> list:
        with self.lock:
            return [body for path, body in self.received if body.get("scan_event") == event]


def _report(tool, _device_id, home_user, _system_user=None, _run_id=None):
    return {"home_user": home_user, "device_id": "THIS-RUN", "system_user": None,
            "tools": [{key: value for key, value in tool.items() if not key.startswith("_")}]}


@unittest.skipIf(os.name == "nt", "POSIX only")
class TheScanAppliesFixesAfterItCompletes(_DrivesMain, unittest.TestCase):
    """main() against a real localhost backend, real curl and the real helper run as this user."""

    def setUp(self):
        super().setUp()
        self.home = Path(os.path.realpath(self._dir))
        self.memory = self.home / ".claude" / "projects" / "-w" / "memory"
        self.memory.mkdir(parents=True)
        self.text = "db token: " + SECRET_A + "\r\n\U0001F511\n"
        (self.memory / "db.md").write_bytes(self.text.encode())

    def run_scan(self, can_fix: bool, pending: bool = True):
        backend = _FixBackend(self.text, pending)
        self.addCleanup(backend.close)
        self.run_main(
            tools=({"name": "Claude Code", "version": "1.0", "install_path": "/usr/local/bin/claude"},),
            users=(ME,), fresh_report=_report, backend=backend,
            patches=(
                patch.object(adm, "_refresh_mcp_tools_cache", Mock()),
                patch.object(adm, "_install_in_another_users_home", return_value=False),
                patch.object(adm, "_machine_global_install_disowned", return_value=False),
                patch.object(adm, "_home_for_user", return_value=self.home),
                patch.object(adm, "find_claude_binary_for_user", return_value=None),
                patch.object(adm, "get_claude_subscription_type", return_value=None),
                patch.object(memory_fix, "supports_fixes", return_value=can_fix),
                patch.object(memory_fix, "POLL_WINDOW_SECONDS", 0),
            ),
        )
        return backend

    def test_the_file_is_fixed_after_the_completed_event_and_the_result_is_posted(self):
        backend = self.run_scan(can_fix=True)

        self.assertEqual(backend.scan_events("in_progress")[0]["capabilities"], ["memory_fix"])
        self.assertEqual((self.memory / "db.md").read_bytes(), self.text.replace(SECRET_A, MARKER).encode())
        posted = backend.posts("/memory-fixes/")[0]["files"]
        self.assertEqual(posted, [{"home_user": ME, "file_path": str(self.memory / "db.md"),
                                   "sha256": _sha(self.text.encode())}])
        self.assertEqual(backend.posts("/memory-fixes/results/")[0]["results"],
                         [{"id": 9, "outcome": "fixed", "token": "T-9"}])
        order = [path for path, body in backend.received
                 if body.get("scan_event") == "completed" or path.endswith("/memory-fixes/")]
        self.assertEqual(order[0], "/api/v1/ai-tools/report/")
        self.assertEqual(sorted(os.listdir(self.memory)), ["db.md"])

    def test_no_capability_and_no_fix_step_when_the_agent_cannot_fix(self):
        backend = self.run_scan(can_fix=False)

        self.assertNotIn("capabilities", backend.scan_events("in_progress")[0])
        self.assertEqual(backend.posts("/memory-fixes/"), [])
        self.assertEqual((self.memory / "db.md").read_bytes(), self.text.encode())

    def test_no_fix_step_when_the_backend_says_nothing_waits(self):
        backend = self.run_scan(can_fix=True, pending=False)

        self.assertEqual(backend.posts("/memory-fixes/"), [])
        self.assertEqual((self.memory / "db.md").read_bytes(), self.text.encode())


if __name__ == "__main__":
    unittest.main()
