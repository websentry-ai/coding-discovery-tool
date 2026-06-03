"""
Integration tests for the full discovery + reporting flow.

Tests the outermost entry points: AIToolsDetector, main() CLI, report_to_sentry,
settings transformation, and project filtering.

Only mocks external environments: HTTP backend (real server on localhost),
QUEUE_FILE path (tempfile), _SENTRY_DSN (prevent real calls), time.sleep.
Tool detection runs un-mocked on whatever OS is available.
"""

import errno
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    report_to_sentry,
    _parse_sentry_dsn,
    _extract_frames,
)
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.settings_transformers import (
    transform_settings_to_backend_format,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _CLIMockHandler(BaseHTTPRequestHandler):
    """Simple handler that records requests and returns configurable status."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(json.loads(body))

        code = self.server.default_code
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass


class TestDetector(unittest.TestCase):
    """Integration tests for AIToolsDetector on the current OS."""

    def test_detector_initializes(self):
        detector = AIToolsDetector()
        device_id = detector.get_device_id()
        self.assertIsInstance(device_id, str)
        self.assertTrue(len(device_id) > 0)

    def test_detect_and_report_flow(self):
        detector = AIToolsDetector()
        device_id = detector.get_device_id()
        tools = detector.detect_all_tools()
        # tools may be empty if no AI tools installed — that's fine
        self.assertIsInstance(tools, list)

        for tool in tools:
            processed = detector.process_single_tool(tool)
            report = detector.generate_single_tool_report(
                processed, device_id, "testuser"
            )
            # Assert required top-level keys
            self.assertIn("home_user", report)
            self.assertIn("system_user", report)
            self.assertIn("device_id", report)
            self.assertIn("tools", report)
            self.assertEqual(len(report["tools"]), 1)

            reported_tool = report["tools"][0]
            self.assertIn("name", reported_tool)
            self.assertIn("projects", reported_tool)


class TestMainCLI(unittest.TestCase):
    """Integration tests that invoke main() via subprocess."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _CLIMockHandler)
        cls.server.requests = []
        cls.server.default_code = 200
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.requests.clear()
        self.server.default_code = 200
        self._queue_file = utils_mod.QUEUE_FILE
        # Ensure clean state
        if self._queue_file.exists():
            self._queue_file.unlink()

    def tearDown(self):
        if self._queue_file.exists():
            self._queue_file.unlink(missing_ok=True)

    def _run_cli(self, extra_env=None, timeout=600):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [
                sys.executable,
                "scripts/coding_discovery_tools/ai_tools_discovery.py",
                "--api-key",
                "test-key-000000",
                "--domain",
                f"http://127.0.0.1:{self.port}",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def test_main_cli_happy_path(self):
        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")


    def test_main_cli_with_queue_drain(self):
        # Pre-populate queue with a distinctive report
        queued_report = {
            "home_user": "queued-user",
            "device_id": "QUEUED-DEVICE",
            "tools": [{"name": "FakeTool", "version": "0.0.0", "projects": []}],
        }
        from datetime import datetime, timezone

        envelope = [
            {
                "report": queued_report,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        self._queue_file.write_text(json.dumps(envelope))

        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Mock server should have received the queued report
        queued_bodies = [
            r for r in self.server.requests if r.get("device_id") == "QUEUED-DEVICE"
        ]
        self.assertGreaterEqual(len(queued_bodies), 1, "Queued report was not drained")


    def test_main_cli_persists_failures(self):
        self.server.default_code = 500

        # Pre-populate queue so there's something to fail
        from datetime import datetime, timezone

        envelope = [
            {
                "report": {"home_user": "fail-user", "device_id": "FAIL", "tools": []},
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        self._queue_file.write_text(json.dumps(envelope))

        result = self._run_cli()
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Queue file should be re-created with failed reports
        self.assertTrue(
            self._queue_file.exists(),
            "Queue file should exist after failures",
        )
        data = json.loads(self._queue_file.read_text())
        self.assertGreaterEqual(len(data), 1)


class TestUnsupportedPlatformGuard(unittest.TestCase):
    """main() runs on supported platforms (macOS/Windows/Linux) and exits
    cleanly on anything else instead of crashing in detector init."""

    def test_linux_proceeds_past_os_guard(self):
        """Linux is supported — it must NOT exit 3 at the OS guard.

        We patch acquire_lock to return "contended" so main() exits 0 at the
        single-flight lock check (the first exit point after the guard).
        Linux reaching that exit-0 proves it passed the OS guard rather
        than hitting the old exit-3.
        """
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        argv = ["ai_tools_discovery.py", "--api-key", "k", "--domain", "http://127.0.0.1:1"]
        with patch.object(adm.platform, "system", return_value="Linux"), \
             patch.object(adm.discovery_cache, "acquire_lock", return_value="contended"), \
             patch.object(adm, "AIToolsDetector"), \
             patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                adm.main()

        # 0 (lock-held early exit), NOT 3 (unsupported-OS guard).
        self.assertEqual(cm.exception.code, 0)

    def test_unsupported_platform_exits_code_3_before_detector_init(self):
        """A genuinely unsupported platform (e.g. *BSD) still exits 3 cleanly
        before detector init, so it can't crash + page Sentry."""
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        argv = ["ai_tools_discovery.py", "--api-key", "k", "--domain", "http://127.0.0.1:1"]
        with patch.object(adm.platform, "system", return_value="FreeBSD"), \
             patch.object(adm, "AIToolsDetector") as mock_detector, \
             patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                adm.main()

        self.assertEqual(cm.exception.code, 3)
        mock_detector.assert_not_called()


class TestSentryNeverCrashes(unittest.TestCase):
    """report_to_sentry must never raise, regardless of input."""

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_empty_dsn(self):
        report_to_sentry(RuntimeError("boom"))

    @patch.object(utils_mod, "_SENTRY_DSN", "not-a-valid-dsn")
    def test_with_bad_dsn(self):
        report_to_sentry(ValueError("bad"))

    @patch.object(utils_mod, "_SENTRY_DSN", "https://key@localhost:1/0")
    def test_with_unreachable_host(self):
        report_to_sentry(IOError("net"), context={"phase": "test"})

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_no_traceback(self):
        exc = RuntimeError("no tb")
        exc.__traceback__ = None
        report_to_sentry(exc)

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_with_none_context(self):
        report_to_sentry(RuntimeError("x"), context=None, level="warning")


class TestParseSentryDsn(unittest.TestCase):
    """_parse_sentry_dsn correctly parses valid DSNs and rejects invalid ones."""

    def test_valid_dsn(self):
        result = _parse_sentry_dsn("https://abc123@o123.ingest.us.sentry.io/456")
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "abc123")
        self.assertEqual(result["host"], "o123.ingest.us.sentry.io")
        self.assertEqual(result["project_id"], "456")
        self.assertEqual(result["store_url"], "https://o123.ingest.us.sentry.io/api/456/store/")

    def test_empty_string(self):
        self.assertIsNone(_parse_sentry_dsn(""))

    def test_malformed_dsn(self):
        self.assertIsNone(_parse_sentry_dsn("not-a-valid-dsn"))

    def test_missing_key(self):
        self.assertIsNone(_parse_sentry_dsn("https://sentry.io/123"))


class TestExtractFrames(unittest.TestCase):
    """_extract_frames returns real frames from raised exceptions and empty list otherwise."""

    def test_exception_with_traceback(self):
        try:
            raise RuntimeError("test error")
        except RuntimeError as exc:
            frames = _extract_frames(exc)
        self.assertGreater(len(frames), 0)
        self.assertIn("filename", frames[0])
        self.assertIn("lineno", frames[0])
        self.assertIn("function", frames[0])

    def test_exception_without_traceback(self):
        exc = RuntimeError("no traceback")
        exc.__traceback__ = None
        self.assertEqual(_extract_frames(exc), [])

    def test_manually_constructed_exception_has_no_frames(self):
        exc = RuntimeError("manual")
        self.assertEqual(_extract_frames(exc), [])


class TestSentryDebugLogging(unittest.TestCase):
    """Sentry helpers log debug messages for missing DSN and curl failures."""

    @patch.object(utils_mod, "_SENTRY_DSN", "")
    def test_logs_debug_when_dsn_missing(self):
        with self.assertLogs("scripts.coding_discovery_tools.utils", level="DEBUG") as cm:
            report_to_sentry(RuntimeError("test"))
        self.assertTrue(any("no valid DSN configured" in msg for msg in cm.output))

    @patch.object(utils_mod, "_SENTRY_DSN", "https://key@localhost:1/0")
    @patch("subprocess.run", side_effect=OSError("connection refused"))
    def test_logs_debug_when_sentry_curl_fails(self, mock_run):
        with self.assertLogs("scripts.coding_discovery_tools.utils", level="DEBUG") as cm:
            report_to_sentry(RuntimeError("test"))
        self.assertTrue(any("Sentry reporting failed" in msg for msg in cm.output))


class TestSettingsTransformPrecedence(unittest.TestCase):
    """Settings transformation picks highest precedence and maps fields correctly."""

    def test_managed_wins_over_user(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/path/user.json",
                "permissions": {"defaultMode": "ask", "allow": ["Read"]},
                "sandbox": {"enabled": False},
            },
            {
                "scope": "managed",
                "settings_path": "/path/managed.json",
                "permissions": {
                    "defaultMode": "deny",
                    "allow": ["Bash"],
                    "deny": ["Write"],
                },
                "sandbox": {"enabled": True},
            },
        ]

        result = transform_settings_to_backend_format(settings)

        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "managed")
        self.assertEqual(result["permission_mode"], "deny")
        self.assertEqual(result["allow_rules"], ["Bash"])
        self.assertEqual(result["deny_rules"], ["Write"])
        self.assertTrue(result["sandbox_enabled"])

    def test_field_mapping(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/s.json",
                "permissions": {
                    "defaultMode": "default",
                    "allow": ["a"],
                    "deny": ["d"],
                    "ask": ["q"],
                    "additionalDirectories": ["/tmp"],
                },
                "sandbox": {"enabled": True},
            }
        ]

        result = transform_settings_to_backend_format(settings)

        self.assertEqual(result["permission_mode"], "default")
        self.assertEqual(result["allow_rules"], ["a"])
        self.assertEqual(result["deny_rules"], ["d"])
        self.assertEqual(result["ask_rules"], ["q"])
        self.assertEqual(result["additional_directories"], ["/tmp"])
        self.assertTrue(result["sandbox_enabled"])

    def test_empty_settings_returns_none(self):
        self.assertIsNone(transform_settings_to_backend_format([]))


class TestFilterProjectsByUser(unittest.TestCase):
    """filter_tool_projects_by_user keeps only paths under the given user home."""

    def test_filters_by_user_home(self):
        detector = AIToolsDetector()
        # Use OS-agnostic paths so str(Path(...)) matches the project path strings
        alice_home = str(Path(tempfile.gettempdir()) / "alice")
        bob_home = str(Path(tempfile.gettempdir()) / "bob")
        tool = {
            "name": "TestTool",
            "version": "1.0",
            "install_path": str(Path(tempfile.gettempdir()) / "bin" / "test"),
            "projects": [
                {"path": os.path.join(alice_home, "project-a"), "rules": [], "mcpServers": []},
                {"path": os.path.join(bob_home, "project-b"), "rules": [], "mcpServers": []},
                {"path": os.path.join(alice_home, "project-c"), "rules": [], "mcpServers": []},
            ],
        }

        filtered = detector.filter_tool_projects_by_user(tool, Path(alice_home))

        self.assertEqual(len(filtered["projects"]), 2)
        paths = [p["path"] for p in filtered["projects"]]
        self.assertIn(os.path.join(alice_home, "project-a"), paths)
        self.assertIn(os.path.join(alice_home, "project-c"), paths)
        self.assertNotIn(os.path.join(bob_home, "project-b"), paths)

    def test_no_matching_projects(self):
        detector = AIToolsDetector()
        alice_home = str(Path(tempfile.gettempdir()) / "alice")
        bob_home = str(Path(tempfile.gettempdir()) / "bob")
        tool = {
            "name": "TestTool",
            "projects": [
                {"path": os.path.join(bob_home, "proj"), "rules": []},
            ],
        }

        filtered = detector.filter_tool_projects_by_user(tool, Path(alice_home))
        self.assertEqual(filtered["projects"], [])


    def test_prefix_collision_excludes_similar_username(self):
        detector = AIToolsDetector()
        # Use OS-agnostic paths via tempdir so separators match on Windows
        base = str(Path(tempfile.gettempdir()) / "Users")
        gowshik_home = os.path.join(base, "gowshik")
        gowshik_2_home = os.path.join(base, "gowshik_2")

        # Build all paths with os.path.join for cross-platform compatibility
        gow_proj1 = os.path.join(gowshik_home, "unbound", "unbound-fe")
        gow_proj2 = os.path.join(gowshik_home, "personal", "blog")
        gow2_proj1 = os.path.join(gowshik_2_home, "unbound", "unbound-fe")
        gow2_settings = os.path.join(gowshik_2_home, ".cursor", "settings.json")

        tool = {
            "name": "TestTool",
            "version": "1.0",
            "projects": [
                {
                    "path": gow_proj1,
                    "mcpServers": [{"name": "sentry"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow_proj1, ".cursorrules"),
                            "content": "be concise",
                        }
                    ],
                    "skills": [
                        {
                            "name": "deploy",
                            "file_path": os.path.join(gow_proj1, ".claude", "skills", "deploy", "skill.md"),
                        }
                    ],
                },
                {
                    "path": gow_proj2,
                    "mcpServers": [{"name": "postgres"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow_proj2, ".cursorrules"),
                            "content": "use markdown",
                        }
                    ],
                    "skills": [
                        {
                            "name": "publish",
                            "file_path": os.path.join(gow_proj2, ".claude", "skills", "publish", "skill.md"),
                        }
                    ],
                },
                {
                    "path": gow2_proj1,
                    "mcpServers": [{"name": "linear"}],
                    "rules": [
                        {
                            "file_path": os.path.join(gow2_proj1, ".cursorrules"),
                            "content": "use TypeScript",
                        }
                    ],
                    "skills": [
                        {
                            "name": "review",
                            "file_path": os.path.join(gow2_proj1, ".claude", "skills", "review", "skill.md"),
                        }
                    ],
                },
            ],
            "permissions": {
                "settings_path": gow2_settings,
                "settings_source": "user",
            },
        }

        # --- Filter for gowshik ---
        filtered_gowshik = detector.filter_tool_projects_by_user(tool, Path(gowshik_home))

        # Only gowshik's 2 projects remain
        self.assertEqual(len(filtered_gowshik["projects"]), 2)

        # gowshik_2's project (with linear MCP server) is NOT included
        filtered_paths = [p["path"] for p in filtered_gowshik["projects"]]
        self.assertNotIn(gow2_proj1, filtered_paths)

        # MCP servers in kept projects are sentry and postgres (not linear)
        mcp_names = [
            s["name"]
            for p in filtered_gowshik["projects"]
            for s in p.get("mcpServers", [])
        ]
        self.assertIn("sentry", mcp_names)
        self.assertIn("postgres", mcp_names)
        self.assertNotIn("linear", mcp_names)

        # Rules in kept projects are gowshik's rules (not gowshik_2's)
        rule_contents = [
            r["content"]
            for p in filtered_gowshik["projects"]
            for r in p.get("rules", [])
        ]
        self.assertIn("be concise", rule_contents)
        self.assertIn("use markdown", rule_contents)
        self.assertNotIn("use TypeScript", rule_contents)

        # Skills in kept projects are gowshik's skills (not gowshik_2's)
        skill_names = [
            s["name"]
            for p in filtered_gowshik["projects"]
            for s in p.get("skills", [])
        ]
        self.assertIn("deploy", skill_names)
        self.assertIn("publish", skill_names)
        self.assertNotIn("review", skill_names)

        # Permissions block is removed (settings_path is under gowshik_2's home)
        self.assertNotIn("permissions", filtered_gowshik)

        # --- Filter for gowshik_2 ---
        filtered_gowshik_2 = detector.filter_tool_projects_by_user(tool, Path(gowshik_2_home))

        # Only gowshik_2's 1 project remains
        self.assertEqual(len(filtered_gowshik_2["projects"]), 1)

        # gowshik's projects are NOT included
        filtered_paths_2 = [p["path"] for p in filtered_gowshik_2["projects"]]
        self.assertNotIn(gow_proj1, filtered_paths_2)
        self.assertNotIn(gow_proj2, filtered_paths_2)

        # MCP server is linear (not sentry or postgres)
        mcp_names_2 = [
            s["name"]
            for p in filtered_gowshik_2["projects"]
            for s in p.get("mcpServers", [])
        ]
        self.assertIn("linear", mcp_names_2)
        self.assertNotIn("sentry", mcp_names_2)
        self.assertNotIn("postgres", mcp_names_2)

        # Permissions block is kept (settings_path is under gowshik_2's home)
        self.assertIn("permissions", filtered_gowshik_2)


class TestAcquireLockReasonCodes(unittest.TestCase):
    """acquire_lock() returns "acquired"/"contended"/"setup_failed" and sets
    last_lock_error only on setup failure."""

    def setUp(self):
        import scripts.coding_discovery_tools.cache as cache
        self.cache = cache
        self._tmp = tempfile.mkdtemp()
        unbound_dir = Path(self._tmp) / ".unbound"
        self._patchers = [
            patch.object(cache, "UNBOUND_DIR", unbound_dir),
            patch.object(cache, "LOCK_PATH", unbound_dir / "discovery.lock"),
            patch.object(cache, "CACHE_PATH", unbound_dir / "discovery-cache.json"),
        ]
        for p in self._patchers:
            p.start()
        cache.last_lock_error = None

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_acquire_lock_setup_failed_on_unwritable_dir(self):
        with patch.object(
            Path, "mkdir", side_effect=OSError(errno.EPERM, "Operation not permitted")
        ):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertTrue(self.cache.last_lock_error)

    def test_acquire_lock_contended_on_fresh_lock(self):
        self.cache.UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
        self.cache.LOCK_PATH.write_text("123 now\n")
        os.utime(self.cache.LOCK_PATH, (time.time(), time.time()))

        self.assertEqual(self.cache.acquire_lock(), "contended")
        self.assertIsNone(self.cache.last_lock_error)

    def test_acquire_lock_acquired_clean(self):
        self.assertEqual(self.cache.acquire_lock(), "acquired")
        self.assertTrue(self.cache.LOCK_PATH.exists())

    def test_acquire_lock_setup_failed_on_steal_stale_unlink_error(self):
        # Create a STALE lock (mtime older than STALE_LOCK_SECONDS) so
        # _lock_is_live() is False and acquire_lock takes the steal/unlink path.
        self.cache.UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
        self.cache.LOCK_PATH.write_text("999 then\n")
        stale = time.time() - (self.cache.STALE_LOCK_SECONDS + 60)
        os.utime(self.cache.LOCK_PATH, (stale, stale))

        # Start the patch AFTER creating the stale file so setup isn't broken.
        with patch.object(
            Path, "unlink", side_effect=OSError(errno.EPERM, "Operation not permitted")
        ):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertTrue(self.cache.last_lock_error)

    def test_acquire_lock_contended_on_open_race(self):
        # TOCTOU race: LOCK_PATH absent at the exists() checks, but os.open with
        # O_CREAT|O_EXCL loses the race and raises FileExistsError.
        with patch.object(self.cache.os, "open", side_effect=FileExistsError()):
            self.assertEqual(self.cache.acquire_lock(), "contended")
        self.assertIsNone(self.cache.last_lock_error)

    def test_acquire_lock_setup_failed_on_write_error(self):
        # mkdir/exists/open succeed on a clean temp dir, but os.write fails.
        with patch.object(self.cache.os, "write", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertTrue(self.cache.last_lock_error)
        # The lock file created by os.open must be removed on write failure, so a
        # ghost lock can't make the next run see false contention.
        self.assertFalse(self.cache.LOCK_PATH.exists())


class TestStateDirFallback(unittest.TestCase):
    """_ensure_state_dir falls back to a deterministic uid-namespaced temp dir
    when home is unusable, refuses hostile pre-existing temp entries, and the
    fallback path is fixed (never random)."""

    def setUp(self):
        import scripts.coding_discovery_tools.cache as cache
        self.cache = cache
        self._tmp = tempfile.mkdtemp()
        # Stash globals the resolver may reassign so tearDown can restore them.
        self._orig_unbound_dir = cache.UNBOUND_DIR
        self._orig_cache_path = cache.CACHE_PATH
        self._orig_lock_path = cache.LOCK_PATH
        cache.last_lock_error = None

    def tearDown(self):
        self.cache.UNBOUND_DIR = self._orig_unbound_dir
        self.cache.CACHE_PATH = self._orig_cache_path
        self.cache.LOCK_PATH = self._orig_lock_path
        self.cache.last_lock_error = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _unmkdir_able(self, name):
        # A candidate under a regular file: mkdir raises NotADirectoryError
        # deterministically and cross-platform.
        blocker = Path(self._tmp) / name
        blocker.write_text("x")
        return Path(blocker) / ".unbound"

    def test_home_unusable_falls_back_to_temp(self):
        bad_home = self._unmkdir_able("blocker")
        good_temp = Path(self._tmp) / "unbound-test"
        with patch.object(
            self.cache, "_state_dir_candidates",
            return_value=[(bad_home, False), (good_temp, True)],
        ):
            self.assertEqual(self.cache.acquire_lock(), "acquired")
        self.assertEqual(self.cache.UNBOUND_DIR, good_temp)
        self.assertTrue(self.cache.LOCK_PATH.exists())

    def test_both_candidates_fail_returns_setup_failed(self):
        bad_a = self._unmkdir_able("blocker_a")
        bad_b = self._unmkdir_able("blocker_b")
        with patch.object(
            self.cache, "_state_dir_candidates",
            return_value=[(bad_a, False), (bad_b, True)],
        ):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertTrue(self.cache.last_lock_error)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_private_temp_symlink_refused(self):
        target = Path(self._tmp) / "elsewhere"
        target.mkdir()
        link = Path(self._tmp) / "unbound-link"
        os.symlink(str(target), str(link))
        with patch.object(
            self.cache, "_state_dir_candidates",
            return_value=[(link, True)],
        ):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertIn("unsafe", self.cache.last_lock_error)

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits only")
    def test_private_temp_created_0700(self):
        new_dir = Path(self._tmp) / "unbound-fresh"
        with patch.object(
            self.cache, "_state_dir_candidates",
            return_value=[(new_dir, True)],
        ):
            self.assertEqual(self.cache.acquire_lock(), "acquired")
        self.assertTrue(new_dir.exists())
        self.assertEqual(stat.S_IMODE(os.lstat(str(new_dir)).st_mode), 0o700)

    def test_temp_candidate_is_deterministic_not_random(self):
        # Regression guard against swapping in mkdtemp(): the temp candidate
        # must be the fixed path. On POSIX it is /var/tmp/unbound-{uid}
        # (cross-session + reboot-stable, NOT the per-session launchd $TMPDIR
        # that gettempdir() would return on macOS); on Windows it is
        # gettempdir()/unbound (already per-user there).
        candidates = self.cache._state_dir_candidates()
        if hasattr(os, "getuid"):
            expected = Path(f"/var/tmp/unbound-{os.getuid()}")
        else:
            expected = Path(tempfile.gettempdir()) / "unbound"
        self.assertEqual(candidates[-1][0], expected)
        self.assertTrue(candidates[-1][1])  # flagged private

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits only")
    @unittest.skipIf(hasattr(os, "getuid") and os.getuid() == 0, "root bypasses W_OK")
    def test_home_exists_but_unwritable_falls_back(self):
        # mkdir succeeds on a pre-existing dir even when it isn't writable;
        # the os.access probe must still force the fallback (and reassign all
        # three path globals), not fail later at lock creation.
        bad_home = Path(self._tmp) / "ro_home"
        bad_home.mkdir()
        os.chmod(str(bad_home), 0o500)
        good_temp = Path(self._tmp) / "unbound-rw"
        try:
            with patch.object(
                self.cache, "_state_dir_candidates",
                return_value=[(bad_home, False), (good_temp, True)],
            ):
                self.assertEqual(self.cache.acquire_lock(), "acquired")
            self.assertEqual(self.cache.UNBOUND_DIR, good_temp)
            self.assertEqual(self.cache.CACHE_PATH, good_temp / "discovery-cache.json")
            self.assertEqual(self.cache.LOCK_PATH, good_temp / "discovery.lock")
        finally:
            os.chmod(str(bad_home), 0o700)

    def test_foreign_owned_temp_dir_refused(self):
        # A pre-existing private candidate owned by someone else (ownership
        # mismatch) must be refused, not trusted. Simulate via _is_unsafe_existing.
        foreign = Path(self._tmp) / "unbound-foreign"
        with patch.object(self.cache, "_is_unsafe_existing", return_value=True), \
             patch.object(
                 self.cache, "_state_dir_candidates",
                 return_value=[(foreign, True)],
             ):
            self.assertEqual(self.cache.acquire_lock(), "setup_failed")
        self.assertTrue(self.cache.last_lock_error)

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits only")
    def test_world_readable_temp_dir_refused(self):
        # A pre-existing 0755 dir whose chmod-to-0700 fails (simulated no-op)
        # leaks discovery state to other users; the post-create mode recheck
        # must refuse it.
        loose = Path(self._tmp) / "unbound-loose"
        loose.mkdir()
        os.chmod(str(loose), 0o755)
        try:
            with patch.object(self.cache.os, "chmod", lambda *a, **k: None), \
                 patch.object(
                     self.cache, "_state_dir_candidates",
                     return_value=[(loose, True)],
                 ):
                self.assertEqual(self.cache.acquire_lock(), "setup_failed")
            self.assertIn("not private", self.cache.last_lock_error)
        finally:
            os.chmod(str(loose), 0o700)

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits only")
    def test_non_sticky_world_writable_parent_refused(self):
        # The symlink/ownership hardening only holds if the parent is sticky.
        # A world-writable, NON-sticky parent lets anyone swap our fixed-name
        # entry, so the candidate must be refused.
        parent = Path(self._tmp) / "open_parent"
        parent.mkdir()
        os.chmod(str(parent), 0o777)  # world-writable, NOT sticky
        candidate = parent / "unbound-x"
        try:
            with patch.object(
                self.cache, "_state_dir_candidates",
                return_value=[(candidate, True)],
            ):
                self.assertEqual(self.cache.acquire_lock(), "setup_failed")
            self.assertTrue(self.cache.last_lock_error)
        finally:
            os.chmod(str(parent), 0o700)

    def test_fallback_cache_writes_land_in_fallback_dir(self):
        # End-to-end coherence of the global reassignment: after falling back to
        # the injected private candidate, cache writes must land UNDER it (via
        # the reassigned CACHE_PATH), not under the original home dir.
        good_temp = Path(self._tmp) / "unbound-fallback"
        with patch.object(
            self.cache, "_state_dir_candidates",
            return_value=[(good_temp, True)],
        ):
            self.assertEqual(self.cache.acquire_lock(), "acquired")
            self.cache.update_tool("claude-code", "u", "hash123")
        self.assertEqual(self.cache.CACHE_PATH, good_temp / "discovery-cache.json")
        self.assertTrue(self.cache.CACHE_PATH.exists())
        # The write must round-trip through the reassigned fallback path, not
        # the original home dir.
        self.assertEqual(
            self.cache.get_cached_hash("claude-code", "u"), "hash123"
        )


class TestMainLockSetupSentry(unittest.TestCase):
    """main() reports lock setup failures to Sentry and exits 0, but stays
    quiet on genuine contention. Sentry reporting can never crash the exit."""

    def setUp(self):
        import scripts.coding_discovery_tools.ai_tools_discovery as adm
        self.adm = adm
        self.argv = [
            "ai_tools_discovery.py", "--api-key", "k", "--domain", "http://127.0.0.1:1"
        ]

    def test_main_reports_sentry_on_lock_setup_failure(self):
        adm = self.adm
        mock_sentry = Mock()
        with patch.object(adm.platform, "system", return_value="Linux"), \
             patch.object(adm.discovery_cache, "acquire_lock", return_value="setup_failed"), \
             patch.object(adm.discovery_cache, "last_lock_error", "EPERM Operation not permitted"), \
             patch.object(adm, "report_to_sentry", mock_sentry), \
             patch.object(adm, "AIToolsDetector") as mock_detector, \
             patch.object(sys, "argv", self.argv):
            with self.assertRaises(SystemExit) as cm:
                adm.main()

        self.assertEqual(cm.exception.code, 0)
        mock_sentry.assert_called_once()
        _, kwargs = mock_sentry.call_args
        self.assertEqual(kwargs["level"], "error")
        ctx = kwargs["context"]
        self.assertEqual(ctx["phase"], "acquire_lock")
        self.assertIn("unbound_dir", ctx)
        self.assertTrue(ctx["lock_error"])
        mock_detector.assert_not_called()

    def test_main_no_sentry_on_genuine_contention(self):
        adm = self.adm
        mock_sentry = Mock()
        with patch.object(adm.platform, "system", return_value="Linux"), \
             patch.object(adm.discovery_cache, "acquire_lock", return_value="contended"), \
             patch.object(adm, "report_to_sentry", mock_sentry), \
             patch.object(adm, "AIToolsDetector"), \
             patch.object(sys, "argv", self.argv):
            with self.assertRaises(SystemExit) as cm:
                adm.main()

        self.assertEqual(cm.exception.code, 0)
        mock_sentry.assert_not_called()

    def test_main_sentry_failure_does_not_crash_exit(self):
        adm = self.adm
        mock_sentry = Mock(side_effect=RuntimeError("boom"))
        with patch.object(adm.platform, "system", return_value="Linux"), \
             patch.object(adm.discovery_cache, "acquire_lock", return_value="setup_failed"), \
             patch.object(adm.discovery_cache, "last_lock_error", "EPERM"), \
             patch.object(adm, "report_to_sentry", mock_sentry), \
             patch.object(adm, "AIToolsDetector"), \
             patch.object(sys, "argv", self.argv):
            with self.assertRaises(SystemExit) as cm:
                adm.main()

        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
