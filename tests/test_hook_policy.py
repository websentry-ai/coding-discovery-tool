"""
WEB-6392 step 4: each tool's report says whether a higher-priority admin policy
can stop Unbound's hook from running.

Seams:
  - TestClaudeCodeHookPolicy / TestCodexHookPolicy / TestVSCodeHookPolicy:
    hook_policy_for(tool_name, home) over fixture files in a temp dir. The system
    locations (managed settings dir, managed preferences dir, Codex system dir)
    are pointed into that temp dir; nothing reads the real machine.
  - TestHookPolicyInReport: main() in-process with the detector mocked and real
    curl sends to a localhost backend; the tool object it receives carries the key.
"""

import json
import os
import plistlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.hook_policy as hook_policy
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.hook_policy import hook_policy_for


def _platform_reporting(system: str):
    """The real platform module with system() faked, for patching one module's ``platform`` name only."""
    import platform as real
    fake = type(real)("platform")
    fake.__dict__.update({k: v for k, v in vars(real).items() if not k.startswith("__")})
    fake.system = lambda: system
    return fake

ALL_NULL_KEYS = ("overriding_policy", "overriding_policy_has_our_hook",
                 "merge_sources_enabled", "hooks_disabled", "telemetry_enabled")


class _FixtureMachine(unittest.TestCase):
    """A temp 'machine': a user home plus the system dirs the module reads."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hook-policy-"))
        self.home = self.root / "Users" / "alice"
        (self.home / ".claude").mkdir(parents=True)
        self.claude_managed = self.root / "ClaudeCode"
        self.claude_managed.mkdir()
        self.prefs = self.root / "Managed Preferences"
        self.prefs.mkdir()
        self.codex_system = self.root / "etc-codex"
        self.codex_system.mkdir()
        self.our_script = self.claude_managed / "hooks" / "unbound.py"
        self.our_script.parent.mkdir()
        self.our_script.write_text("#!/usr/bin/env python3\n")
        self.our_script.chmod(0o755)  # as setup installs it
        self.unbound_root = self.root / "opt" / "unbound"
        self.our_binary = self.unbound_root / "current" / "unbound-hook" / "unbound-hook"
        self.our_binary.parent.mkdir(parents=True)
        self.our_binary.write_text("")
        self._stack = ExitStack()
        for p in (
            patch.object(hook_policy, "platform", Mock(system=Mock(return_value="Darwin"))),  # this module only
            patch.dict(hook_policy.CLAUDE_MANAGED_DIRS, {"Darwin": self.claude_managed}),
            patch.dict(hook_policy.CODEX_SYSTEM_DIRS, {"Darwin": self.codex_system}),
            patch.object(hook_policy, "MANAGED_PREFERENCES_DIR", self.prefs),
            patch.object(hook_policy, "UNBOUND_ROOT", self.unbound_root),
        ):
            self._stack.enter_context(p)

    def tearDown(self):
        self._stack.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))

    def hooks_running(self, command: str) -> dict:
        return {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}

    def our_hook_command(self) -> str:
        return f'"{self.our_script}"'  # as setup writes it on macOS and Linux


class TestClaudeCodeHookPolicy(_FixtureMachine):

    def console(self, data) -> None:
        self.write_json(self.home / ".claude" / "remote-settings.json", data)

    def our_managed_file(self, data=None) -> None:
        self.write_json(self.claude_managed / "managed-settings.json",
                        data or {"hooks": self.hooks_running(self.our_hook_command())})

    def test_console_policy_that_carries_our_hook(self):
        self.our_managed_file()
        self.console({"hooks": self.hooks_running(self.our_hook_command())})

        self.assertEqual(hook_policy_for("Claude Code", self.home), {
            "overriding_policy": "console",
            "overriding_policy_has_our_hook": True,
            "merge_sources_enabled": False,
            "hooks_disabled": False,
            "telemetry_enabled": None,
        })

    def test_console_policy_without_our_hook_that_merges_and_turns_telemetry_on(self):
        self.our_managed_file()
        self.console({
            "hooks": self.hooks_running("/usr/local/bin/someone-elses-hook"),
            "managedSourcesBehavior": "merge",
            "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"},
        })

        self.assertEqual(hook_policy_for("Claude Code", self.home), {
            "overriding_policy": "console",
            "overriding_policy_has_our_hook": False,
            "merge_sources_enabled": True,
            "hooks_disabled": False,
            "telemetry_enabled": True,
        })

    def test_guarded_exec_of_our_script_counts_as_our_hook(self):
        # How admins write it in the console so machines without Unbound skip it.
        self.console({"hooks": self.hooks_running(
            f'if [ -f "{self.our_script}" ]; then exec "{self.our_script}"; fi')})

        self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    @unittest.skipIf(os.name == "nt", "Windows has no execute bit")
    def test_a_directly_run_script_without_its_execute_bit_is_not_a_working_hook(self):
        self.our_script.chmod(0o644)
        self.console({"hooks": self.hooks_running(self.our_hook_command())})
        self.assertFalse(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

        # Through an interpreter the bit is not needed.
        self.console({"hooks": self.hooks_running(f'python3 "{self.our_script}"')})
        self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_our_script_only_named_as_an_argument_is_not_our_hook(self):
        self.console({"hooks": self.hooks_running(f'/usr/bin/cat "{self.our_script}"')})

        self.assertFalse(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_per_user_forms_of_our_user_script_count_when_installed_for_that_user(self):
        # An org-wide console policy can't hold a per-user absolute path.
        user_script = self.home / ".claude" / "hooks" / "unbound.py"
        user_script.parent.mkdir()
        user_script.write_text("#!/usr/bin/env python3\n")
        user_script.chmod(0o755)  # as setup installs it
        for command in ('"$HOME/.claude/hooks/unbound.py"', "${HOME}/.claude/hooks/unbound.py",
                        "~/.claude/hooks/unbound.py",
                        'if [ -f "$HOME/.claude/hooks/unbound.py" ]; then exec "$HOME/.claude/hooks/unbound.py"; fi'):
            self.console({"hooks": self.hooks_running(command)})
            self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"], command)
        user_script.unlink()
        self.assertFalse(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_our_script_run_through_env_or_a_versioned_interpreter_counts(self):
        for command in (f'/usr/bin/env python3 "{self.our_script}"', f'python3.11 "{self.our_script}"',
                        f'/usr/bin/python3 -u "{self.our_script}"', f'py -3 "{self.our_script}"'):
            self.console({"hooks": self.hooks_running(command)})
            self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"], command)

    def test_unbound_hook_binary_counts_as_our_hook(self):
        self.console({"hooks": self.hooks_running(
            f'"{self.our_binary}" hook claude-code PreToolUse')})

        self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_any_installed_unbound_hook_binary_under_opt_unbound_counts(self):
        # setup accepts any /opt/unbound/... unbound-hook, e.g. a pinned version dir.
        pinned = self.unbound_root / "0.1.46" / "unbound-hook" / "unbound-hook"
        pinned.parent.mkdir(parents=True)
        pinned.write_text("")
        self.console({"hooks": self.hooks_running(f'"{pinned}" hook claude-code PreToolUse')})
        self.assertTrue(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

        missing = self.unbound_root / "0.1.40" / "unbound-hook" / "unbound-hook"
        self.console({"hooks": self.hooks_running(f'"{missing}" hook claude-code PreToolUse')})
        self.assertFalse(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_our_hook_that_is_not_installed_here_does_not_count(self):
        # The policy names Unbound's script, but this machine has the binary
        # install, so the guarded line runs nothing and no Unbound hook fires.
        self.our_script.unlink()
        self.console({"hooks": self.hooks_running(
            f'if [ -f "{self.our_script}" ]; then exec "{self.our_script}"; fi')})

        self.assertFalse(hook_policy_for("Claude Code", self.home)["overriding_policy_has_our_hook"])

    def test_mdm_profile_that_disables_all_hooks(self):
        self.our_managed_file()
        # A user-level profile; macOS gives it priority over the computer-level one.
        with open(self.prefs / "com.anthropic.claudecode.plist", "wb") as f:
            plistlib.dump({"disableAllHooks": False, "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "0"}}, f)
        (self.prefs / "alice").mkdir()
        with open(self.prefs / "alice" / "com.anthropic.claudecode.plist", "wb") as f:
            plistlib.dump({"disableAllHooks": True}, f)

        self.assertEqual(hook_policy_for("Claude Code", self.home), {
            "overriding_policy": "mdm",
            "overriding_policy_has_our_hook": False,
            "merge_sources_enabled": False,
            "hooks_disabled": True,
            "telemetry_enabled": False,
        })

    def test_only_our_managed_file(self):
        self.our_managed_file({"hooks": self.hooks_running(self.our_hook_command()),
                               "disableAllHooks": False})

        self.assertEqual(hook_policy_for("Claude Code", self.home), {
            "overriding_policy": None,
            "overriding_policy_has_our_hook": None,
            "merge_sources_enabled": False,
            "hooks_disabled": False,
            "telemetry_enabled": None,
        })

    def test_drop_ins_merge_nested_blocks_key_by_key(self):
        # Claude Code merges managed-settings.d into managed-settings.json key by
        # key for nested blocks such as env, so a drop-in's env adds variables.
        self.our_managed_file({"hooks": self.hooks_running(self.our_hook_command()),
                               "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}})
        self.write_json(self.claude_managed / "managed-settings.d" / "20-proxy.json",
                        {"env": {"HTTPS_PROXY": "http://proxy.example.test:3128"}})

        self.assertTrue(hook_policy_for("Claude Code", self.home)["telemetry_enabled"])

    def test_control_keys_alone_do_not_make_a_source_a_policy(self):
        self.our_managed_file()
        self.console({"managedSourcesBehavior": "merge"})

        policy = hook_policy_for("Claude Code", self.home)
        # The console carries no policy key, so it doesn't override, but its
        # merge opt-in is the one Claude Code reads.
        self.assertIsNone(policy["overriding_policy"])
        self.assertTrue(policy["merge_sources_enabled"])

    def test_user_settings_decide_hooks_disabled_when_nothing_is_managed(self):
        self.write_json(self.home / ".claude" / "settings.json", {"disableAllHooks": True})

        policy = hook_policy_for("Claude Code", self.home)
        self.assertTrue(policy["hooks_disabled"])
        self.assertIsNone(policy["overriding_policy"])

    def test_no_policy_anywhere_is_not_determinable(self):
        # No cached console policy is not proof there is none (it's cached only once approved).
        self.assertIsNone(hook_policy_for("Claude Code", self.home))

    def test_unreadable_console_cache_fails_open(self):
        self.our_managed_file()
        self.console("{ not json")

        self.assertIsNone(hook_policy_for("Claude Code", self.home))

    def test_unreadable_lower_source_only_nulls_what_depends_on_it(self):
        # The console has a policy key, so it wins whatever the broken file says.
        self.write_json(self.claude_managed / "managed-settings.json", "{ not json")
        self.console({"hooks": self.hooks_running(self.our_hook_command())})

        self.assertEqual(hook_policy_for("Claude Code", self.home), {
            "overriding_policy": "console",
            "overriding_policy_has_our_hook": True,
            "merge_sources_enabled": False,
            "hooks_disabled": False,
            # Unset in the console, so it could come from the file we can't read.
            "telemetry_enabled": None,
        })

        self.console({"hooks": self.hooks_running(self.our_hook_command()),
                      "env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}})
        self.assertTrue(hook_policy_for("Claude Code", self.home)["telemetry_enabled"])

    def test_unreadable_lower_source_nulls_keys_merge_would_take_from_it(self):
        self.write_json(self.claude_managed / "managed-settings.json", "{ not json")
        self.console({"hooks": self.hooks_running(self.our_hook_command()),
                      "managedSourcesBehavior": "merge"})

        policy = hook_policy_for("Claude Code", self.home)
        self.assertEqual(policy["overriding_policy"], "console")
        self.assertTrue(policy["merge_sources_enabled"])
        self.assertIsNone(policy["hooks_disabled"])  # the file's disableAllHooks would merge in

    def test_windows_hklm_policy_is_mdm(self):
        with patch.object(hook_policy, "platform", Mock(system=Mock(return_value="Windows"))), \
             patch.dict(hook_policy.CLAUDE_MANAGED_DIRS, {"Windows": self.claude_managed}), \
             patch.object(hook_policy, "_read_hklm_claude_settings",
                          return_value=json.dumps({"disableAllHooks": True})):
            policy = hook_policy_for("Claude Code", self.home)

        self.assertEqual(policy["overriding_policy"], "mdm")
        self.assertTrue(policy["hooks_disabled"])


class TestCodexHookPolicy(_FixtureMachine):

    def config(self, text: str) -> None:
        self.write_json(self.home / ".codex" / "config.toml", text)

    def test_hooks_feature_turned_off(self):
        self.config('model = "gpt-5"\n\n[features]\nhooks = false\n')

        self.assertEqual(hook_policy_for("Codex", self.home), {
            "overriding_policy": None, "overriding_policy_has_our_hook": None,
            "merge_sources_enabled": None, "hooks_disabled": True, "telemetry_enabled": None,
        })

    def test_deprecated_alias_is_honoured(self):
        self.config("[features]\ncodex_hooks = false\n")

        self.assertTrue(hook_policy_for("Codex", self.home)["hooks_disabled"])

    def test_requirements_pin_overrides_the_user(self):
        self.config("[features]\nhooks = false\n")
        self.write_json(self.codex_system / "requirements.toml", "[features]\nhooks = true\n")

        self.assertFalse(hook_policy_for("Codex", self.home)["hooks_disabled"])

    def test_hooks_are_on_by_default(self):
        self.config('model = "gpt-5"\n')

        self.assertFalse(hook_policy_for("Codex", self.home)["hooks_disabled"])

    def test_commented_table_header_is_read_by_both_parsers(self):
        for text in ("[features] # feature flags\nhooks = false\n", "[features]  # see [docs\nhooks = false\n"):
            self.assertIs(hook_policy._toml_hooks_feature_by_line(text), False, text)
            self.config(text)
            self.assertTrue(hook_policy_for("Codex", self.home)["hooks_disabled"], text)

    def test_dotted_and_inline_features_keys_are_read_by_both_parsers(self):
        cases = {
            'model = "gpt-5"\nfeatures.hooks = false\n': False,
            "features.codex_hooks = false  # old name\n": False,
            "features = { hooks = false, web_search = true }\n": False,
            "features = { web_search = true, codex_hooks = true }  # on\n": True,
            # Quoted keys are the same keys in TOML.
            '[ "features" ]\n"hooks" = false\n': False,
            "[features]\n'codex_hooks' = true\n": True,
            '"features".hooks = false\n': False,
            "features.'hooks' = false\n": False,
            'features = { "hooks" = false }\n': False,
            # Under another table the same text names a different key, so it decides nothing.
            "[profile]\nfeatures.hooks = false\n": None,
            # Text inside a multi-line string is a value, not a table or a flag; a real flag after it still counts.
            'instructions = """\n[features]\nhooks = false\n"""\n': None,
            "notes = '''\nfeatures.hooks = false'''\n": None,
            'prompt = """one line"""\n[features]\nhooks = false\n': False,
            'instructions = """\n[features]\nhooks = false\n"""\n[features]\nhooks = true\n': True,
            # Triple quotes in a comment, or inside a one-line string, open nothing.
            'model = "gpt-5"  # see """ below\n[features]\nhooks = false\n': False,
            "title = \"say '''hi'''\"\n[features]\nhooks = false\n": False,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertIs(hook_policy._toml_hooks_feature_by_line(text), expected)
                self.config(text)
                policy = hook_policy_for("Codex", self.home)
                self.assertIs(policy["hooks_disabled"], expected is False)

    def test_broken_config_fails_open(self):
        self.config("[features\nhooks = false\n")

        self.assertIsNone(hook_policy_for("Codex", self.home))


class TestVSCodeHookPolicy(_FixtureMachine):

    def settings(self, text: str) -> None:
        self.write_json(self.home / "Library" / "Application Support" / "Code" / "User" / "settings.json", text)

    def test_use_hooks_false_disables_hooks(self):
        self.settings('{\n  // mine\n  "chat.useHooks": false,\n}\n')

        for name in ("GitHub Copilot Chat (VS Code)", "GitHub Copilot (VS Code)"):
            self.assertEqual(hook_policy_for(name, self.home), {
                "overriding_policy": None, "overriding_policy_has_our_hook": None,
                "merge_sources_enabled": None, "hooks_disabled": True, "telemetry_enabled": None,
            }, name)

    def test_unset_is_not_determinable(self):
        self.settings('{"editor.fontSize": 14}')

        self.assertIsNone(hook_policy_for("GitHub Copilot Chat (VS Code)", self.home))

    def test_other_editors_and_tools_get_nothing(self):
        self.settings('{"chat.useHooks": false}')

        self.assertIsNone(hook_policy_for("GitHub Copilot Chat (Cursor)", self.home))
        self.assertIsNone(hook_policy_for("Cursor", self.home))


class _ReportServer(BaseHTTPRequestHandler):
    """The backend for main(): records each JSON body by path. The S3 upload path is refused, so data
    reports take the direct POST that carries them."""

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if "upload-url" in self.path:
            self.send_response(404)
            self.end_headers()
            return
        try:
            self.server.bodies.append((self.path, json.loads(body or b"{}")))
        except ValueError:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "queued"}')

    def log_message(self, *args):
        pass


class _DiscoveryRun(_FixtureMachine):
    """main() in-process with the detector mocked and real sends to a localhost backend."""

    def _run_main(self, *more):
        """main() over a Claude Code and a Zed install for alice; returns the bodies the backend received."""
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        tools = [{"name": "Claude Code", "install_path": "/x/claude", "version": "1"},
                 {"name": "Zed", "install_path": "/x/zed", "version": "1"}]
        detector = Mock()
        detector.get_device_id.return_value = "dev"
        detector.detect_all_tools.return_value = tools
        detector.process_single_tool.return_value = {"projects": []}
        detector.filter_tool_projects_by_user.side_effect = lambda tool, *a, **k: {
            "name": tool["name"], "projects": [], "version": "1", "install_path": tool["install_path"]}
        real_report = adm.AIToolsDetector.generate_single_tool_report  # before the class is mocked
        detector.generate_single_tool_report.side_effect = lambda *a: real_report(None, *a)
        server = HTTPServer(("127.0.0.1", 0), _ReportServer)
        server.bodies = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        argv = ["ai_tools_discovery.py", "--api-key", "k", "--domain", f"http://127.0.0.1:{server.server_address[1]}"]
        patches = [
            patch.object(adm, "platform", _platform_reporting("Darwin")),  # this module only
            patch.object(adm.discovery_cache, "acquire_lock", return_value="acquired"),
            patch.object(adm.discovery_cache, "heartbeat_start", Mock(return_value=threading.Event())),
            patch.object(adm.discovery_cache, "release_lock", Mock()),
            patch.object(adm.discovery_cache, "start_run", Mock()),
            patch.object(adm.discovery_cache, "resumable_done", Mock(return_value=set())),
            patch.object(adm.discovery_cache, "mark_run_completed", Mock()),
            patch.object(adm.discovery_cache, "get_cached_hash", Mock(return_value=None)),
            patch.object(adm.discovery_cache, "record_report", Mock()),
            patch.object(adm, "AIToolsDetector", return_value=detector),
            patch.object(adm, "_home_for_user", return_value=self.home),
            patch.object(adm, "_tool_for_user", side_effect=lambda d, tool, *a: {
                "name": tool["name"], "projects": [], "version": "1",
                "install_path": tool["install_path"]}),
            patch.object(adm, "find_claude_binary_for_user", return_value=None),
            patch.object(adm, "get_claude_subscription_type", return_value=None),
            patch.object(adm, "run_sweep", Mock(return_value=(0, 0, 0))),
            patch.object(adm, "load_pending_reports", return_value=[]),
            patch.object(adm, "save_failed_reports", Mock()),
            patch.object(adm, "get_all_users_macos", return_value=["alice"]),
            patch.object(adm, "home_is_readable", return_value=True),
            patch.object(adm, "get_audit_user", return_value="alice"),
            patch.object(adm, "report_to_sentry", Mock()),
            patch.object(utils_mod, "_SENTRY_DSN", ""),
            patch.object(sys, "argv", argv),
        ]
        with ExitStack() as stack:
            for p in patches + list(more):
                stack.enter_context(p)
            try:
                adm.main()
            except SystemExit:
                pass
        return list(server.bodies)

    @staticmethod
    def _sent_tools(bodies) -> dict:
        by_tool = {}
        for _, report in bodies:
            for tool in report.get("tools") or []:
                by_tool[tool["name"]] = tool
        return by_tool


class TestHookPolicyInReport(_DiscoveryRun):
    """The policy reaches the backend on the tool object it describes."""

    def test_claude_code_report_carries_hook_policy_and_other_tools_do_not(self):
        self.write_json(self.home / ".claude" / "remote-settings.json",
                        {"hooks": self.hooks_running(self.our_hook_command())})
        by_tool = self._sent_tools(self._run_main())
        self.assertEqual(set(by_tool), {"Claude Code", "Zed"}, by_tool)
        self.assertEqual(by_tool["Claude Code"]["hook_policy"]["overriding_policy"], "console")
        self.assertTrue(by_tool["Claude Code"]["hook_policy"]["overriding_policy_has_our_hook"])
        self.assertNotIn("hook_policy", by_tool["Zed"])


class TestDetectorNamesGetTheirPolicy(_FixtureMachine):
    """hook_policy_for keys on the names the detectors emit. A detector that renames its row would
    silently drop the policy, so the names come from the real detectors, on every OS."""

    def test_claude_code_and_codex_detector_names_get_a_policy(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        self.write_json(self.home / ".claude" / "settings.json", {"disableAllHooks": False})
        for os_name in ("Darwin", "Windows", "Linux"):
            for detector in (ToolDetectorFactory.create_claude_detector(os_name),
                             ToolDetectorFactory.create_codex_detector(os_name)):
                with self.subTest(os=os_name, tool=detector.tool_name):
                    policy = hook_policy_for(detector.tool_name, self.home)
                    self.assertIsNotNone(policy)
                    self.assertIs(policy["hooks_disabled"], False)

    def _copilot_names(self, detector_cls):
        """Every row name the detector gives a VS Code or Cursor Copilot install, marketplace and built-in."""
        import importlib
        detector = detector_cls()
        users = self.root / "detect" / detector_cls.__name__
        names = set()
        # Marketplace: both extensions in VS Code and in Cursor.
        for ext_dir in (".vscode/extensions", ".cursor/extensions"):
            registry = users / "marketplace" / ext_dir / "extensions.json"
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text(json.dumps([{"identifier": {"id": "github.copilot"}, "version": "1.0.0"},
                                            {"identifier": {"id": "github.copilot-chat"}, "version": "0.30.0"}]))
        names |= {row["name"] for row in detector._detect_vscode_for_user(users / "marketplace")}
        # Built-in: the bundled folder as Copilot Chat and as plain Copilot.
        module = importlib.import_module(detector_cls.__module__)
        for package in ({"name": "copilot-chat", "version": "0.30.0"}, {"name": "copilot", "version": "1.0.0"}):
            app_root = users / "app" / package["name"]
            (app_root / "copilot").mkdir(parents=True)
            (app_root / "copilot" / "package.json").write_text(json.dumps(package))
            if hasattr(detector, "_vscode_app_extension_roots"):  # Windows
                roots = patch.object(detector, "_vscode_app_extension_roots", return_value=[app_root])
            elif hasattr(module, "_app_extension_roots"):  # macOS
                roots = patch.object(module, "_app_extension_roots", return_value=[app_root])
            else:  # Linux
                roots = patch.object(module, "_VSCODE_APP_EXTENSION_ROOTS", [app_root])
            with roots:
                row = detector._bundled_copilot_row(users / "marketplace")
            names.add(row["name"])
        return names

    def test_vscode_copilot_rows_get_a_policy_and_other_editors_do_not(self):
        from scripts.coding_discovery_tools.linux.github_copilot.detect_copilot import LinuxCopilotDetector
        from scripts.coding_discovery_tools.macos.github_copilot.detect_copilot import MacOSCopilotDetector
        from scripts.coding_discovery_tools.windows.github_copilot.detect_copilot import WindowsGitHubCopilotDetector

        self.write_json(self.home / "Library" / "Application Support" / "Code" / "User" / "settings.json",
                        {"chat.useHooks": False})
        for detector_cls in (MacOSCopilotDetector, LinuxCopilotDetector, WindowsGitHubCopilotDetector):
            with self.subTest(detector_cls.__name__):
                names = self._copilot_names(detector_cls)
                self.assertTrue({"GitHub Copilot (VS Code)", "GitHub Copilot Chat (VS Code)"} <= names, names)
                for name in names:
                    policy = hook_policy_for(name, self.home)
                    if name.endswith("(VS Code)"):
                        self.assertEqual(policy and policy["hooks_disabled"], True, name)
                    else:
                        self.assertIsNone(policy, name)


class TestHostileFiles(_FixtureMachine):
    """Discovery runs as root over files users write. None of them may raise, hang or take long."""

    def _within_a_second(self, tool_name):
        result, errors = [], []

        def call():
            try:
                result.append(hook_policy_for(tool_name, self.home))
            except BaseException as e:  # hook_policy_for promises never to raise
                errors.append(e)
        worker = threading.Thread(target=call, daemon=True)
        began = time.monotonic()
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), f"{tool_name} policy read hung")
        self.assertEqual(errors, [])
        self.assertLess(time.monotonic() - began, 1.0)
        return result[0]

    def test_files_that_cannot_be_policies_never_break_the_read(self):
        nested = "[" * 200000 + "]" * 200000
        cases = [
            ("an oversized console cache", "Claude Code", self.home / ".claude" / "remote-settings.json",
             '{"a": "' + "x" * (2 * 1024 * 1024) + '"}'),
            ("deeply nested user settings", "Claude Code", self.home / ".claude" / "settings.json",
             '{"disableAllHooks": ' + nested + "}"),
            ("deeply nested managed file", "Claude Code", self.claude_managed / "managed-settings.json", nested),
            ("binary garbage MDM profile", "Claude Code", self.prefs / "com.anthropic.claudecode.plist",
             b"bplist00\xff\xfe\x00garbage"),
            ("binary garbage Codex profile", "Codex", self.prefs / "com.openai.codex.plist", b"\x00\x01\x02\x03"),
            ("deeply nested Codex config", "Codex", self.home / ".codex" / "config.toml",
             "[features]\nhooks = " + "[" * 200000 + "]" * 200000 + "\n"),
            ("deeply nested VS Code settings", "GitHub Copilot (VS Code)",
             self.home / "Library" / "Application Support" / "Code" / "User" / "settings.json", nested),
        ]
        for label, tool, path, content in cases:
            with self.subTest(label):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content)
                try:
                    policy = self._within_a_second(tool)
                    self.assertTrue(policy is None or isinstance(policy, dict))
                finally:
                    path.unlink()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on this OS")
    def test_a_fifo_in_place_of_a_policy_file_never_blocks(self):
        fifos = [("Claude Code", self.home / ".claude" / "remote-settings.json"),
                 ("Claude Code", self.home / ".claude" / "settings.json"),
                 ("Claude Code", self.claude_managed / "managed-settings.json"),
                 ("Claude Code", self.claude_managed / "managed-settings.d" / "10-fifo.json"),
                 ("Codex", self.home / ".codex" / "config.toml"),
                 ("GitHub Copilot (VS Code)",
                  self.home / "Library" / "Application Support" / "Code" / "User" / "settings.json")]
        for tool, path in fifos:
            with self.subTest(str(path.relative_to(self.root))):
                path.parent.mkdir(parents=True, exist_ok=True)
                os.mkfifo(path)
                try:
                    self._within_a_second(tool)
                finally:
                    path.unlink()


class TestPayloadHashAcrossTheUpgrade(_DiscoveryRun):
    """hook_policy joins the hashed tool object, so the first run after the upgrade re-uploads each tool
    that has a policy, once. After that the hash must hold still, or every run re-uploads."""

    def test_the_first_new_run_reuploads_policy_tools_once_and_the_next_dedups(self):
        import scripts.coding_discovery_tools.ai_tools_discovery as adm

        self.write_json(self.home / ".claude" / "remote-settings.json",
                        {"hooks": self.hooks_running(self.our_hook_command())})
        cache = [patch.object(adm.discovery_cache, "CACHE_PATH", self.root / "discovery-cache.json"),
                 patch.object(adm.discovery_cache, "get_cached_hash", adm.discovery_cache.get_cached_hash),
                 patch.object(adm.discovery_cache, "record_report", adm.discovery_cache.record_report),
                 patch.object(adm, "_refresh_mcp_tools_cache", Mock())]
        # The released discovery: the same tool objects, without hook_policy.
        released = self._sent_tools(self._run_main(*cache, patch.object(adm, "hook_policy_for", return_value=None)))
        self.assertEqual(set(released), {"Claude Code", "Zed"})
        self.assertNotIn("hook_policy", released["Claude Code"])

        first = self._sent_tools(self._run_main(*cache))
        self.assertEqual(set(first), {"Claude Code"}, "only the tool whose payload changed goes up again")
        self.assertIn("hook_policy", first["Claude Code"])
        self.assertEqual(self._sent_tools(self._run_main(*cache)), {}, "an unchanged policy must dedup")


if __name__ == "__main__":
    unittest.main()
