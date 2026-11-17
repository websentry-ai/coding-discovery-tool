"""Every tool through one simulated admin scan: each user's report holds only that user's data."""

import contextlib
import json
import os
import pathlib
import shutil
import platform
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import scripts.coding_discovery_tools.ai_tools_discovery as atd
import scripts.coding_discovery_tools.cache as cache_mod
import scripts.coding_discovery_tools.constants as constants_mod
import scripts.coding_discovery_tools.linux_extraction_helpers as linux_helpers_mod
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.coding_tool_base import BaseCursorSettingsExtractor
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

PACKAGE = "scripts.coding_discovery_tools"
REAL_PATH = pathlib.Path
# Absolute roots the code hardcodes; all of them land inside the fake machine.
MACHINE_ROOTS = ("/Users", "/home", "/root", "/var/root", "/Library", "/Applications", "/etc",
                 "/opt", "/snap", "/var/lib", "/var/tmp", "/usr/share", "/usr/local")
LAYOUTS = {
    "Darwin": {"users_root": "Users", "scanner_home": "var/root"},
    "Linux": {"users_root": "home", "scanner_home": "root"},
}


def marker(user: str) -> str:
    return f"zz{user}zz"


def _skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: skill {name}\n---\n# {name}\n"


def _mcp(name: str) -> dict:
    return {name: {"command": "echo", "args": [name]}}


def fixture_files(os_name: str) -> dict:
    """Tool name -> {path relative to home: content}; {u} is the user marker, {h} the home."""
    app_support = "Library/Application Support" if os_name == "Darwin" else ".config"
    vscode_user = f"{app_support}/Code/User"
    return {
        "Claude Code": {
            ".claude/CLAUDE.md": "# rule {u}\n",
            ".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(echo {u})"]}}),
            ".claude/skills/{u}-skill/SKILL.md": _skill("{u}-skill"),
            ".claude.json": json.dumps({"mcpServers": _mcp("{u}-mcp"),
                                         "projects": {"{h}/proj-{u}": {"mcpServers": _mcp("{u}-projmcp")}}}),
            ".claude/plugins/installed_plugins.json": json.dumps({"version": 2, "plugins": {
                "{u}-plugin@mkt": [{"scope": "user", "version": "1.0.0",
                                    "installPath": "{h}/.claude/plugins/cache/mkt/{u}-plugin/1.0.0"}]}}),
            ".claude/plugins/cache/mkt/{u}-plugin/1.0.0/.claude-plugin/plugin.json": json.dumps({"name": "{u}-plugin"}),
            ".claude/plugins/cache/mkt/{u}-plugin/1.0.0/skills/{u}-pskill/SKILL.md": _skill("{u}-pskill"),
            "proj-{u}/CLAUDE.md": "# project rule {u}\n",
            "proj-{u}/.mcp.json": json.dumps({"mcpServers": _mcp("{u}-dotmcp")}),
            "proj-{u}/.claude/settings.local.json": json.dumps({"permissions": {"deny": ["Bash(rm {u})"]}}),
            "proj-{u}/.claude/skills/{u}-projskill/SKILL.md": _skill("{u}-projskill"),
        },
        "Claude Cowork": {
            f"{app_support}/Claude/local-agent-mode-sessions/skills-plugin/org/acct/skills/{{u}}-cowork/SKILL.md":
                _skill("{u}-cowork"),
        },
        "Claude Desktop": {
            f"{app_support}/Claude/claude_desktop_config.json": json.dumps({"mcpServers": _mcp("{u}-desktop")}),
        },
        "Cursor": {
            f"{app_support}/Cursor/User/globalStorage/state.vscdb": json.dumps(
                {"composerState": {"useYoloMode": True, "yoloCommandAllowlist": ["{u}"]}}),
            ".cursor/mcp.json": json.dumps({"mcpServers": _mcp("{u}-cursormcp")}),
            ".cursor/skills/{u}-cskill/SKILL.md": _skill("{u}-cskill"),
            ".cursor/plugins/cache/mkt/{u}-cplugin/abc123/.cursor-plugin/plugin.json": json.dumps({"name": "{u}-cplugin"}),
            ".cursor/plugins/cache/mkt/{u}-cplugin/abc123/skills/{u}-cpskill/SKILL.md": _skill("{u}-cpskill"),
            "proj-{u}/.cursor/rules/{u}.mdc": "---\nalwaysApply: true\n---\nrule {u}\n",
            "proj-{u}/.cursor/mcp.json": json.dumps({"mcpServers": _mcp("{u}-cprojmcp")}),
        },
        "Cursor CLI": {
            ".cursor/cli-config.json": json.dumps({"permissions": {"allow": ["Shell({u})"], "deny": []}}),
        },
        "Codex": {
            ".codex/config.toml": '[mcp_servers.{u}-codex]\ncommand = "echo"\nargs = ["{u}"]\n',
            ".codex/AGENTS.md": "# codex rule {u}\n",
            ".codex/skills/{u}-xskill/SKILL.md": _skill("{u}-xskill"),
        },
        "Gemini CLI": {
            ".gemini/settings.json": json.dumps({"mcpServers": _mcp("{u}-gemini")}),
            ".gemini/GEMINI.md": "# gemini rule {u}\n",
            ".gemini/skills/{u}-gskill/SKILL.md": _skill("{u}-gskill"),
        },
        "Antigravity": {
            ".gemini/antigravity/mcp_config.json": json.dumps({"mcpServers": _mcp("{u}-antigravity")}),
        },
        "Devin Desktop": {
            ".codeium/windsurf/mcp_config.json": json.dumps({"mcpServers": _mcp("{u}-windsurf")}),
            ".codeium/windsurf/memories/global_rules.md": "windsurf rule {u}\n",
            "proj-{u}/.windsurf/rules/{u}.md": "windsurf project rule {u}\n",
        },
        "OpenCode": {
            ".config/opencode/opencode.json": json.dumps({"mcp": {"{u}-opencode": {"type": "local",
                                                                                   "command": ["echo", "{u}"]}}}),
            ".config/opencode/AGENTS.md": "# opencode rule {u}\n",
        },
        "Cline": {
            f"{vscode_user}/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json":
                json.dumps({"mcpServers": _mcp("{u}-cline")}),
            "proj-{u}/.clinerules/{u}.md": "cline rule {u}\n",
        },
        "Roo Code": {
            f"{vscode_user}/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json":
                json.dumps({"mcpServers": _mcp("{u}-roo")}),
            "proj-{u}/.roo/rules/{u}.md": "roo rule {u}\n",
        },
        "Kilo Code": {
            f"{vscode_user}/globalStorage/kilocode.kilo-code/settings/mcp_settings.json":
                json.dumps({"mcpServers": _mcp("{u}-kilo")}),
            "proj-{u}/.kilocode/rules/{u}.md": "kilo rule {u}\n",
        },
        "GitHub Copilot": {
            f"{vscode_user}/mcp.json": json.dumps({"servers": _mcp("{u}-vscodemcp")}),
            f"{vscode_user}/settings.json": json.dumps({"chat.tools.autoApprove": True, "zz": "{u}"}),
            f"{vscode_user}/prompts/{{u}}.instructions.md": "copilot rule {u}\n",
            "proj-{u}/.github/copilot-instructions.md": "copilot project rule {u}\n",
        },
        "GitHub Copilot CLI": {
            ".copilot/mcp-config.json": json.dumps({"mcpServers": _mcp("{u}-copilotcli")}),
            ".copilot/copilot-instructions.md": "copilot cli rule {u}\n",
            ".copilot/skills/{u}-ccskill/SKILL.md": _skill("{u}-ccskill"),
        },
        "Zed": {
            ".config/zed/settings.json": json.dumps({"context_servers": _mcp("{u}-zed")}),
            "proj-{u}/.rules": "zed rule {u}\n",
        },
        "Junie": {
            ".junie/mcp/mcp.json": json.dumps({"mcpServers": _mcp("{u}-junie")}),
            "proj-{u}/.junie/guidelines.md": "junie rule {u}\n",
        },
    }


# A registered tool must have a fixture or a reason here, so a new tool cannot skip the check.
EXEMPT = {
    "Xcode Coding Intelligence": "no per-user artifacts extracted",
    "GitHub Copilot (Xcode)": "UserDefaults suites; not faked yet",
    "Replit": "not faked yet",
    "Pi Coding Agent": "not faked yet",
    "Grok Bot": "not faked yet",
    "Muse": "presence only",
    "Muse Code": "not faked yet",
    "OpenClaw": "presence only",
    "Augment Code": "not faked yet",
    "JetBrains IDEs": "not faked yet",
    "GitHub Copilot App": "presence only",
    "Visual Studio": "Windows registry; not faked yet",
}


def _rewrite_root(fake_root: str, arg):
    if not isinstance(arg, str):
        return arg
    posix = arg.replace("\\", "/")
    if len(posix) >= 2 and posix[1] == ":":
        posix = posix[2:]
    if posix == "/":
        return fake_root
    for root in MACHINE_ROOTS:
        if posix == root or posix.startswith(root + "/"):
            return fake_root + posix
    return arg


def make_path_shim(fake_root: str):
    """Stands in for ``Path`` in package modules; returns real paths with machine roots redirected."""
    class _Meta(type):
        def __call__(cls, *args, **kwargs):
            if args:
                args = (_rewrite_root(fake_root, args[0]),) + args[1:]
            return REAL_PATH(*args, **kwargs)

        def __getattr__(cls, name):
            return getattr(REAL_PATH, name)

        def __instancecheck__(cls, obj):
            return isinstance(obj, REAL_PATH)

    return _Meta("Path", (), {})


# Captured before any patch: module constants built from Path.home() at import point here.
IMPORT_TIME_HOME = REAL_PATH.home()


def package_modules():
    return [m for name, m in list(sys.modules.items()) if m and name.startswith(PACKAGE)]


def patch_everywhere(stack: contextlib.ExitStack, name: str, replacement) -> None:
    # `from x import f` binds f per module, so patch every binding.
    for module in package_modules():
        if name in vars(module):
            stack.enter_context(mock.patch.object(module, name, replacement))


def rebase_import_time_paths(stack: contextlib.ExitStack, new_home, fake_root: str) -> None:
    """Point module/class Path constants built at import (home or machine roots) into the fake machine."""
    def rebased(value):
        if not isinstance(value, REAL_PATH):
            return None
        try:
            return REAL_PATH(new_home, value.relative_to(IMPORT_TIME_HOME))
        except ValueError:
            moved = _rewrite_root(fake_root, str(value))
            return REAL_PATH(moved) if moved != str(value) else None

    for module in package_modules():
        for owner in [module] + [c for c in vars(module).values()
                                 if isinstance(c, type) and c.__module__ == module.__name__]:
            for attr, value in list(vars(owner).items()):
                target = rebased(value)
                if target is not None:
                    stack.enter_context(mock.patch.object(owner, attr, target))


def iter_leaves(value, trail=()):
    """(field path without list indices, string) for every string in a report."""
    if isinstance(value, str):
        yield "/".join(trail), value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield "/".join(trail + (str(key),)), str(key)
            yield from iter_leaves(item, trail + (str(key),))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from iter_leaves(item, trail)


def foreign_hits(report: dict, foreign_markers, foreign_homes) -> list:
    homes = [atd._normalise_path(str(h)) for h in foreign_homes]
    hits = []
    for field, text in iter_leaves(report):
        norm = text.replace("\\", "/")
        if any(m in text for m in foreign_markers) or any(norm == h or (h + "/") in norm for h in homes):
            hits.append(f"{field}={text[:120]}")
    return hits


def own_leaves(report: dict, user: str, home) -> set:
    """Fields carrying the user's marker, with the home path normalised away."""
    home_norm = atd._normalise_path(str(home))
    return {(field, text.replace("\\", "/").replace(home_norm, "~")) for field, text in iter_leaves(report)
            if marker(user) in text and "scanned_at" not in field}


def write_cursor_state_db(target, storage_json: str) -> None:
    with contextlib.closing(sqlite3.connect(str(target))) as conn, conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        conn.execute("INSERT INTO ItemTable VALUES (?, ?)", (BaseCursorSettingsExtractor.STORAGE_KEY, storage_json))


class FakeMachine:
    """Temp tree laid out like a multi-user machine; each user gets the same fixture with their marker."""

    def __init__(self, test: unittest.TestCase, os_name: str, users, home_names=None):
        self.os_name, self.users = os_name, list(users)
        self.fake_root = os.path.realpath(tempfile.mkdtemp(prefix="admin-scan-"))  # macOS /var is a symlink
        test.addCleanup(shutil.rmtree, self.fake_root, ignore_errors=True)
        if _rewrite_root(self.fake_root, self.fake_root) != self.fake_root:
            test.skipTest(f"temp dir {self.fake_root} sits under a simulated machine root")
        layout = LAYOUTS[os_name]
        self.homes = {u: REAL_PATH(self.fake_root, layout["users_root"], u) for u in self.users}
        self.scanner_home = REAL_PATH(self.fake_root, layout["scanner_home"])
        for home in [self.scanner_home, *self.homes.values()]:
            home.mkdir(parents=True, exist_ok=True)
        self.fixtures = fixture_files(os_name)

    def home_of(self, user: str):
        return self.homes.get(user, self.scanner_home)

    def write_fixture(self, user: str, tools=None) -> None:
        home = self.home_of(user)
        fill = lambda s: s.replace("{u}", marker(user)).replace("{h}", str(home).replace("\\", "/"))
        for tool, files in self.fixtures.items():
            if tools is not None and tool not in tools:
                continue
            for rel, content in files.items():
                target = home / fill(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.suffix == ".vscdb":
                    write_cursor_state_db(target, fill(content))
                else:
                    target.write_text(fill(content), encoding="utf-8")

    def has_fixture(self, user: str, tool_name: str) -> bool:
        home = self.homes[user]
        return any((home / rel.replace("{u}", marker(user))).exists() for rel in self.fixtures.get(tool_name, {}))


@contextlib.contextmanager
def scan_as(machine: FakeMachine, scanner=None):
    """Patch the process to scan ``machine`` as root (scanner=None) or as one user."""
    # Factories import OS modules lazily; load them all before patching their bindings.
    for os_name in LAYOUTS:
        AIToolsDetector(os_name=os_name)
    is_root = scanner is None
    home = machine.scanner_home if is_root else machine.homes[scanner]
    homes, users = dict(machine.homes), list(machine.users)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, {
            "HOME": str(home), "USERPROFILE": str(home), "AI_DISCOVERY_SENTRY_DSN": "", "PATH": "/usr/bin:/bin"}))
        stack.enter_context(mock.patch.object(utils_mod, "_SENTRY_DSN", ""))
        stack.enter_context(mock.patch.object(platform, "system", return_value=machine.os_name))
        stack.enter_context(mock.patch.object(cache_mod, "_HOME_STATE_DIR", REAL_PATH(machine.fake_root, "state")))
        stack.enter_context(mock.patch.object(atd, "_scan_user_homes", lambda: list(homes.values())))
        stack.enter_context(mock.patch.object(atd, "_home_for_user", machine.home_of))
        # Simulated identity, whatever account runs the test (some CI jobs run as root).
        if hasattr(os, "getuid"):
            stack.enter_context(mock.patch.object(os, "getuid", return_value=0 if is_root else 501))
            stack.enter_context(mock.patch.object(os, "geteuid", return_value=0 if is_root else 501))
        patch_everywhere(stack, "_windows_process_is_elevated", lambda: is_root)
        # Spotlight sees this machine's apps and is missing off macOS; report a search that found nothing.
        patch_everywhere(stack, "spotlight_claude_bundles", lambda *args, **kwargs: ([], True))
        # MDM plist preferences are read with `defaults`, which would see this machine's.
        stack.enter_context(mock.patch(f"{PACKAGE}.macos.claude_code.settings_extractor."
                                       "MacOSClaudeSettingsExtractor._extract_plist_settings", return_value=[]))
        patch_everywhere(stack, "Path", make_path_shim(machine.fake_root))
        rebase_import_time_paths(stack, home, machine.fake_root)
        for name in ("is_running_as_root", "_is_root", "_running_as_root"):
            patch_everywhere(stack, name, lambda: is_root)
        patch_everywhere(stack, "get_all_users_macos", lambda: users if machine.os_name == "Darwin" else [])
        patch_everywhere(stack, "get_all_users_linux", lambda: users if machine.os_name == "Linux" else [])
        patch_everywhere(stack, "get_all_users_windows", lambda: [])
        patch_everywhere(stack, "windows_user_homes", lambda: {})
        # Like the real one, includes root's own home (/root) under a root scan.
        patch_everywhere(stack, "get_linux_user_homes",
                         lambda: [*homes.values(), machine.scanner_home] if is_root else [home])
        patch_everywhere(stack, "linux_home_for_user", machine.home_of)
        # Never launch or connect to configured MCP servers.
        patch_everywhere(stack, "scan_mcp_server", lambda cfg, *a, **k: {"status": "skipped_in_test"})
        # The macOS root walk skips /private, /var, /tmp, where the temp machine lives.
        skip_dirs = [machine.fake_root + d for d in constants_mod.SKIP_SYSTEM_DIRS]
        patch_everywhere(stack, "SKIP_SYSTEM_DIRS", skip_dirs)
        patch_everywhere(stack, "_LINUX_SKIP_SYSTEM_DIRS",
                         frozenset(machine.fake_root + d for d in linux_helpers_mod._LINUX_SKIP_SYSTEM_DIRS))
        yield


def detect_for_user(detector: AIToolsDetector, machine: FakeMachine, user: str) -> list:
    """Real per-user detection; a synthetic row for a fixture tool whose detector needs a binary or app bundle."""
    rows = []
    for tool_detector in detector._tool_detectors:
        found = atd.detect_tool_for_user(tool_detector, machine.homes[user])
        if found:
            rows.extend(found if isinstance(found, list) else [found])
        elif machine.has_fixture(user, tool_detector.tool_name):
            rows.append({"name": tool_detector.tool_name, "version": "1.0",
                         "install_path": "/Applications/" + tool_detector.tool_name, "_synthetic": True})
    return rows


def build_user_reports(machine: FakeMachine) -> dict:
    """main()'s detection and per-user report steps without the network: {user: {tool name: tool report}}."""
    detector = AIToolsDetector(os_name=machine.os_name)
    homes = machine.homes
    all_homes = list(homes.values())
    tools, by_key, manifest = [], {}, set()
    for user in machine.users:
        for tool in detect_for_user(detector, machine, user):
            manifest.add(atd._install_key(user, tool))
            key = f"{tool.get('name')}:{tool.get('install_path')}"
            if key not in by_key:
                by_key[key] = tool
                tools.append(tool)
            atd._keep_user_version(by_key[key], tool, homes[user])
    detector._set_canonical_vscode_copilot(tools)
    detector._set_canonical_augment_surface(tools)
    detector._set_canonical_junie_surface(tools)
    reports = {user: {} for user in homes}
    for tool in tools:
        name = tool.get("name")
        processed = detector.process_single_tool(tool)
        for user, home in homes.items():
            if atd._install_in_another_users_home(tool, home, all_homes):
                continue
            filtered = atd._tool_for_user(detector, tool, processed, user, home, manifest)
            if filtered is None:
                continue
            key = f"{name} @ {tool.get('install_path')}" if name in reports[user] else name
            reports[user][key] = detector.generate_single_tool_report(filtered, "dev", user)["tools"][0]
    return reports


def registered_tools(os_name: str) -> set:
    return {d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)}


def merged_by_name(reports_for_user: dict) -> dict:
    merged = {}
    for key, report in reports_for_user.items():
        merged.setdefault(key.split(" @ ")[0], []).append(report)
    return merged


def isolation_problems(machine: FakeMachine, admin: dict, user_scans: dict) -> list:
    """LEAK: a report names another user or root. DROP: data the user's own scan reports that the admin scan loses."""
    problems = []
    for user in machine.users:
        others = [u for u in machine.users if u != user]
        for tool_name, report in sorted(admin[user].items()):
            hits = foreign_hits(report, [marker(u) for u in others] + [marker("root")],
                                [machine.homes[u] for u in others] + [machine.scanner_home])
            if hits:
                fields = sorted({hit.split("=")[0] for hit in hits})
                problems.append(f"LEAK {tool_name} -> {user}: {fields}")
        if user not in user_scans:
            continue
        admin_by_name = merged_by_name(admin[user])
        for tool_name, reports in sorted(merged_by_name(user_scans[user]).items()):
            expected = set().union(*(own_leaves(r, user, machine.homes[user]) for r in reports))
            got = set().union(*(own_leaves(r, user, machine.homes[user]) for r in admin_by_name.get(tool_name, [])))
            missing = sorted({field for field, _ in expected - got})
            if missing:
                problems.append(f"DROP {tool_name} -> {user}: {missing[:4]}")
    return problems


# Each tool here must report permissions for every user with data, in both scan modes.
PERMISSION_TOOLS = ("Claude Code", "Cursor", "Cursor CLI")
# Fixture tool -> the report names its data may arrive under.
REPORT_NAMES = {"GitHub Copilot": ("GitHub Copilot", "GitHub Copilot Chat (VS Code)")}


class _AdminScanChecks:
    OS_NAME = "Darwin"

    def run_scans(self, users=("alice", "bob"), data_users=None, root_data=False, tools=None, machine_files=None):
        machine = FakeMachine(self, self.OS_NAME, users)
        data_users = list(users) if data_users is None else list(data_users)
        for user in data_users:
            machine.write_fixture(user, tools)
        if root_data:
            machine.write_fixture("root", tools)
        for rel, content in (machine_files or {}).items():
            target = REAL_PATH(machine.fake_root, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        with scan_as(machine):
            admin = build_user_reports(machine)
        user_scans = {}
        for user in data_users:
            with scan_as(machine, scanner=user):
                user_scans[user] = build_user_reports(machine)[user]
        return machine, admin, user_scans

    def assert_isolated(self, **kwargs):
        machine, admin, user_scans = self.run_scans(**kwargs)
        tools = set(kwargs.get("tools") or machine.fixtures) & registered_tools(self.OS_NAME)
        # Some readers check an open file's path through OS-specific calls (/proc), so only a native host reaches all.
        native_tools = tools if self.OS_NAME == platform.system() else set()
        for user in user_scans:
            self.assertTrue(any(own_leaves(r, user, machine.homes[user]) for r in user_scans[user].values()),
                            f"fixture produced no data for {user}; harness is not reaching the extractors")
            by_name = merged_by_name(user_scans[user])
            for tool_name in native_tools:
                reports = [r for name in REPORT_NAMES.get(tool_name, (tool_name,)) for r in by_name.get(name, [])]
                self.assertTrue(any(own_leaves(r, user, machine.homes[user]) for r in reports),
                                f"{tool_name} fixture produced no data for {user}; harness is not reaching it")
            for tool_name in set(PERMISSION_TOOLS) & tools:
                for reports in (user_scans[user], admin[user]):
                    self.assertTrue(any(r.get("permissions") for r in merged_by_name(reports).get(tool_name, [])),
                                    f"{tool_name} has no permissions for {user}")
        self.assertEqual(isolation_problems(machine, admin, user_scans), [])
        for reports in [*admin.values(), *user_scans.values()]:
            for tool_name, report in reports.items():
                self.assertEqual([k for k in report if k.startswith("_")], [], f"{tool_name}: helper key reported")
                perms = report.get("permissions") or {}
                for settings_path in [perms.get("settings_path"), *(perms.get("contributing_paths") or [])]:
                    if settings_path:
                        self.assertTrue(settings_path.startswith(machine.fake_root),
                                        f"{tool_name}: {settings_path} is outside the fake machine")
        return machine, admin, user_scans

    def test_two_users_both_with_data(self):
        self.assert_isolated()

    def test_one_user_without_data(self):
        self.assert_isolated(data_users=["alice"])

    def test_home_names_that_prefix_each_other(self):
        self.assert_isolated(users=("al", "alice"))

    def test_data_in_scanner_home_reaches_no_user(self):
        self.assert_isolated(root_data=True)

    def test_riskier_settings_in_scanner_home_reach_no_user(self):
        root_settings = {f"{LAYOUTS[self.OS_NAME]['scanner_home']}/.claude/settings.json":
                         json.dumps({"permissions": {"allow": ["Bash"], "defaultMode": "bypassPermissions"}})}
        self.assert_isolated(root_data=True, tools=["Claude Code"], machine_files=root_settings)


class TestEveryToolHasAFixture(unittest.TestCase):
    def test_registered_tools_are_covered_or_exempt(self):
        for os_name in LAYOUTS:
            missing = registered_tools(os_name) - set(fixture_files(os_name)) - set(EXEMPT)
            self.assertEqual(missing, set(), f"{os_name}: add a fixture or an EXEMPT reason")


@unittest.skipIf(os.name == "nt", "simulates POSIX homes and uids; Windows paths are not faked")
class TestDarwinAdminScanIsolation(_AdminScanChecks, unittest.TestCase):
    OS_NAME = "Darwin"

    def test_managed_settings_do_not_carry_other_users_rules(self):
        managed = {"Library/Application Support/ClaudeCode/managed-settings.json":
                   json.dumps({"permissions": {"deny": ["Bash(curl:*)"]}})}
        self.assert_isolated(machine_files=managed, tools=["Claude Code"])

    def test_riskier_settings_outside_every_users_home_reach_no_user(self):
        # The Linux walk only visits listed homes, so only the macOS walk can pick these up.
        risky = json.dumps({"permissions": {"allow": ["Bash"], "defaultMode": "bypassPermissions"}})
        stray = {"Users/Shared/.claude/settings.json": risky, "Users/Shared/proj/.claude/settings.json": risky}
        self.assert_isolated(tools=["Claude Code"], machine_files=stray)


@unittest.skipIf(os.name == "nt", "simulates POSIX homes and uids; Windows paths are not faked")
class TestLinuxAdminScanIsolation(_AdminScanChecks, unittest.TestCase):
    OS_NAME = "Linux"


if __name__ == "__main__":
    unittest.main()
