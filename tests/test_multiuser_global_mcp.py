"""
Regression tests for multi-user global MCP config accumulation under root.

Bug: ``extract_global_mcp_config_with_root_support`` (and the OpenCode sibling
``extract_opencode_global_mcp_config_with_root_support``) returned the FIRST
user's global MCP config and silently DROPPED every other user's when run as
root/admin on a multi-user macOS/Windows machine. The culprit was an early
``return config`` inside the per-user loop, where Claude Code's
``extract_dual_path_configs_with_root_support`` correctly ``.extend()``s.

Fix: the helpers now ACCUMULATE each user's config into a list (de-duplicated by
the config's ``path`` key) and keep the root/admin's own home as a FALLBACK ONLY
(used only when no per-user config was found), preserving the original
single-user-root semantics exactly.

These tests lock that in:
  - multi-user root recovers BOTH users' servers (today only the first survived);
  - the single-user / non-root path still yields exactly one config (1-element
    list — byte-identical content to the pre-fix single dict);
  - de-dup collapses the same path seen twice (the Windows admin-own-home case).

The buggy code lives in the per-user loop, so we isolate it via the
``_iter_admin_user_homes`` seam (which already accumulates correctly) and a
patched ``Path.home()``; this keeps the test deterministic and independent of
real root privileges or a real ``/Users`` tree.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.mcp_extraction_helpers as helpers
from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    extract_global_mcp_config_with_root_support,
)
import scripts.coding_discovery_tools.macos.opencode.mcp_config_extractor as oc_macos
import scripts.coding_discovery_tools.windows.opencode.mcp_config_extractor as oc_windows
import scripts.coding_discovery_tools.toml_mcp_helpers as toml_helpers
from scripts.coding_discovery_tools.toml_mcp_helpers import (
    extract_codex_global_mcp_config_with_admin_support,
)


def _write_cursor_mcp(home: Path, server_name: str) -> None:
    """Create ``<home>/.cursor/mcp.json`` with a single distinct server."""
    cursor_dir = home / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            server_name: {"command": "npx", "args": ["-y", server_name]}
        }
    }))


def _write_opencode_mcp(home: Path, server_name: str) -> None:
    """Create ``<home>/.config/opencode/opencode.json`` with one distinct server."""
    oc_dir = home / ".config" / "opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    (oc_dir / "opencode.json").write_text(json.dumps({
        "mcp": {"mcpServers": {server_name: {"command": "echo", "args": [server_name]}}}
    }))


def _write_opencode_mcp_windows(home: Path, server_name: str) -> None:
    r"""Create ``<home>\AppData\Roaming\.config\opencode\opencode.json`` with one
    distinct stdio server.

    Mirrors the real Windows layout that ``WindowsOpenCodeMCPConfigExtractor``
    resolves (``AppData\Roaming\.config\opencode\opencode.json`` -> 5 levels up =
    home), so the helper's ``parent_levels=5`` path math lands the config's
    ``path`` key exactly on the per-user home. Uses a ``command``/``args`` stdio
    server (no ``url``) so nothing touches the network — hermetic, like the macOS
    OpenCode and Cursor writers above.
    """
    oc_dir = home / "AppData" / "Roaming" / ".config" / "opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    (oc_dir / "opencode.json").write_text(json.dumps({
        "mcp": {"mcpServers": {server_name: {"command": "echo", "args": [server_name]}}}
    }))


def _write_codex_mcp(home: Path, server_name: str) -> None:
    """Create ``<home>/.codex/config.toml`` with one distinct ``[mcp_servers.*]``.

    Codex stores MCP config in TOML (not JSON), so this is the shape
    ``read_codex_toml_mcp_config`` / ``parse_toml_mcp_servers`` expects. Uses a
    stdio ``command``/``args`` server (no ``url``) so the live scanner
    short-circuits — keeping the test hermetic and fast, exactly like the
    ``command``-based JSON servers above.
    """
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(
        f'[mcp_servers.{server_name}]\n'
        f'command = "npx"\n'
        f'args = ["-y", "{server_name}"]\n'
    )


def _server_names(configs):
    return {s["name"] for cfg in configs for s in cfg["mcpServers"]}


class TestMultiUserRootAccumulates(unittest.TestCase):
    """Root/admin scan must accumulate every user's global config, not just the
    first. Isolated at the ``_iter_admin_user_homes`` seam (which accumulates),
    so it is the per-user loop body under test."""

    def test_both_users_servers_are_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            bob = users / "bob"
            _write_cursor_mcp(alice, "alice-server")
            _write_cursor_mcp(bob, "bob-server")

            # Pretend "alice" is the home we resolve relative paths against; the
            # admin scan iterates both alice and bob.
            with mock.patch.object(helpers.Path, "home", return_value=alice), \
                 mock.patch.object(
                     helpers, "_iter_admin_user_homes", return_value=[alice, bob]
                 ):
                configs = extract_global_mcp_config_with_root_support(
                    alice / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        self.assertEqual(len(configs), 2, "both users' configs must be collected")
        # The exact bug: pre-fix only "alice-server" survived.
        self.assertEqual(_server_names(configs), {"alice-server", "bob-server"})
        # Distinct per-user paths are preserved.
        self.assertEqual({c["path"] for c in configs}, {str(alice), str(bob)})

    def test_dedup_collapses_same_path_seen_twice(self):
        """De-dup by ``path`` (the Windows admin-own-home double-count case):
        the same user dir listed twice yields a single config."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            _write_cursor_mcp(alice, "alice-server")

            with mock.patch.object(helpers.Path, "home", return_value=alice), \
                 mock.patch.object(
                     helpers, "_iter_admin_user_homes", return_value=[alice, alice]
                 ):
                configs = extract_global_mcp_config_with_root_support(
                    alice / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        self.assertEqual(len(configs), 1, "duplicate path must collapse to one")
        self.assertEqual(_server_names(configs), {"alice-server"})


class TestSingleUserAndNonRootUnchanged(unittest.TestCase):
    """Strictly-additive guarantee: single-user and non-root output is a
    0-or-1 element list whose content matches the pre-fix single dict."""

    def test_non_root_yields_single_config(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"
            _write_cursor_mcp(home, "solo-server")

            # Non-root: _iter_admin_user_homes returns [] regardless of is_admin.
            with mock.patch.object(helpers.Path, "home", return_value=home), \
                 mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[]):
                configs = extract_global_mcp_config_with_root_support(
                    home / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(home))
        self.assertEqual(_server_names(configs), {"solo-server"})

    def test_non_root_missing_config_yields_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"  # no .cursor/mcp.json written
            home.mkdir(parents=True)

            with mock.patch.object(helpers.Path, "home", return_value=home), \
                 mock.patch.object(helpers, "_iter_admin_user_homes", return_value=[]):
                configs = extract_global_mcp_config_with_root_support(
                    home / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        self.assertEqual(configs, [])

    def test_root_fallback_to_admin_own_home_when_no_user_config(self):
        """When admin homes exist but none has a config, the admin's own
        ``global_config_path`` is the fallback — exactly one config, matching the
        original single-user-root behavior (the fallback is NOT always-added)."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            empty_a = users / "empty_a"
            empty_b = users / "empty_b"
            empty_a.mkdir(parents=True)
            empty_b.mkdir(parents=True)
            admin_home = users / "admin"
            _write_cursor_mcp(admin_home, "admin-server")  # admin's OWN config

            with mock.patch.object(helpers.Path, "home", return_value=admin_home), \
                 mock.patch.object(
                     helpers, "_iter_admin_user_homes", return_value=[empty_a, empty_b]
                 ):
                configs = extract_global_mcp_config_with_root_support(
                    admin_home / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        self.assertEqual(len(configs), 1)
        self.assertEqual(_server_names(configs), {"admin-server"})

    def test_root_fallback_not_added_when_user_config_present(self):
        """Fallback is suppressed once any per-user config is found, so the
        admin's own home is never double-counted alongside real user configs."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            admin_home = users / "admin"
            _write_cursor_mcp(alice, "alice-server")
            _write_cursor_mcp(admin_home, "admin-server")

            with mock.patch.object(helpers.Path, "home", return_value=admin_home), \
                 mock.patch.object(
                     helpers, "_iter_admin_user_homes", return_value=[alice]
                 ):
                configs = extract_global_mcp_config_with_root_support(
                    admin_home / ".cursor" / "mcp.json",
                    tool_name="Cursor",
                    parent_levels=2,
                )

        # Only the real per-user config; admin's own home is the fallback and is
        # suppressed because alice's config was found.
        self.assertEqual(_server_names(configs), {"alice-server"})


class TestCodexMultiUserRoot(unittest.TestCase):
    """Codex has its OWN copy of the helper because it stores MCP config in TOML
    (``~/.codex/config.toml``) rather than JSON. It had the identical first-match
    bug (``return config`` inside the per-user loop); same accumulate + dedup +
    fallback-only fix.

    Unlike OpenCode, the codex helper is gated on the injected ``is_admin_fn``
    callable (not ``platform.system()`` directly), so the admin branch is driven
    on ANY OS by passing ``is_admin_fn -> (True, <temp users>)``. ``Path.home()``
    is patched on the ``toml_mcp_helpers`` module so per-user relative-path
    resolution stays inside the temp tree (mirrors the Cursor test's seam).
    """

    def test_both_users_servers_are_recovered_codex(self):
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            bob = users / "bob"
            _write_codex_mcp(alice, "alice-codex")
            _write_codex_mcp(bob, "bob-codex")

            # alice is the home we resolve relative paths against; the admin scan
            # iterates the whole temp users tree (both alice and bob).
            with mock.patch.object(toml_helpers.Path, "home", return_value=alice):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    alice / ".codex" / "config.toml",
                    is_admin_fn=lambda: (True, users),
                )

        self.assertEqual(len(configs), 2, "both users' codex configs must be collected")
        # The exact bug: pre-fix only the first user's server survived.
        self.assertEqual(_server_names(configs), {"alice-codex", "bob-codex"})
        # Distinct per-user paths (~/.codex) are preserved.
        self.assertEqual({c["path"] for c in configs}, {str(alice / ".codex"), str(bob / ".codex")})

    def test_dedup_collapses_same_path_seen_twice_codex(self):
        """De-dup by ``path`` (the Windows admin-own-home double-count case): the
        same user dir surfacing twice yields a single config."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            alice_dup = users / "alice"  # same path, listed again
            _write_codex_mcp(alice, "alice-codex")

            class _DupIterUsers:
                """Stand in for ``users_dir`` so ``iterdir`` lists alice twice
                while ``is_dir``/``exists`` defer to the real temp tree."""

                def exists(self_inner):
                    return users.exists()

                def iterdir(self_inner):
                    return iter([alice, alice_dup])

            with mock.patch.object(toml_helpers.Path, "home", return_value=alice):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    alice / ".codex" / "config.toml",
                    is_admin_fn=lambda: (True, _DupIterUsers()),
                )

        self.assertEqual(len(configs), 1, "duplicate path must collapse to one")
        self.assertEqual(_server_names(configs), {"alice-codex"})

    def test_single_user_non_admin_yields_one_element_list_codex(self):
        """Strictly-additive: non-admin yields a 1-element list whose content
        matches the pre-fix single dict (output unchanged for the common case)."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"
            _write_codex_mcp(home, "solo-codex")

            with mock.patch.object(toml_helpers.Path, "home", return_value=home):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    home / ".codex" / "config.toml",
                    is_admin_fn=lambda: (False, None),
                )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(home / ".codex"))
        self.assertEqual(_server_names(configs), {"solo-codex"})

    def test_non_admin_missing_config_yields_empty_list_codex(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"  # no ~/.codex/config.toml written
            home.mkdir(parents=True)

            with mock.patch.object(toml_helpers.Path, "home", return_value=home):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    home / ".codex" / "config.toml",
                    is_admin_fn=lambda: (False, None),
                )

        self.assertEqual(configs, [])

    def test_root_fallback_to_admin_own_home_when_no_user_config_codex(self):
        """Admin homes exist but none has a codex config -> the admin's own
        ``global_config_path`` is the fallback (exactly one config), matching the
        original single-user-root behavior. The fallback is NOT always-added.

        The admin's home is kept OUTSIDE the scanned ``users`` tree so the only
        way ``admin-codex`` can surface is via the post-loop fallback, not the
        per-user pickup."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"          # scanned tree: only empty homes
            (users / "empty_a").mkdir(parents=True)
            (users / "empty_b").mkdir(parents=True)
            admin_home = Path(td) / "admin_home"  # NOT under the scanned tree
            _write_codex_mcp(admin_home, "admin-codex")  # admin's OWN config

            with mock.patch.object(toml_helpers.Path, "home", return_value=admin_home):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    admin_home / ".codex" / "config.toml",
                    is_admin_fn=lambda: (True, users),
                )

        self.assertEqual(len(configs), 1)
        self.assertEqual(_server_names(configs), {"admin-codex"})

    def test_root_fallback_suppressed_when_user_config_present_codex(self):
        """Fallback is suppressed once any per-user config is found, so the
        admin's own home is never double-counted alongside real user configs."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            _write_codex_mcp(alice, "alice-codex")          # real per-user config
            admin_home = Path(td) / "admin_home"            # outside scanned tree
            _write_codex_mcp(admin_home, "admin-codex")     # admin's OWN config

            with mock.patch.object(toml_helpers.Path, "home", return_value=admin_home):
                configs = extract_codex_global_mcp_config_with_admin_support(
                    admin_home / ".codex" / "config.toml",
                    is_admin_fn=lambda: (True, users),
                )

        # Only the real per-user config; the admin's own home is the fallback and
        # is suppressed because alice's config was found.
        self.assertEqual(_server_names(configs), {"alice-codex"})


class TestOpenCodeMultiUserRoot(unittest.TestCase):
    """OpenCode has its own copy of the helper (it inlines the ``/Users`` walk
    instead of using ``_iter_admin_user_homes``); same accumulate + dedup fix.

    The helper hardcodes ``users_dir = Path("/Users")``, so we patch the module's
    ``Path`` symbol with a thin wrapper that redirects only that one literal to a
    temp users tree, and force ``is_running_as_root`` -> True. This test only
    runs where ``platform.system() == "Darwin"`` (the helper's admin branch is
    Darwin-gated); it skips elsewhere rather than asserting a no-op.
    """

    def test_both_users_servers_recovered_opencode(self):
        import platform
        if platform.system() != "Darwin":
            self.skipTest("macOS OpenCode admin branch is Darwin-gated")

        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            bob = users / "bob"
            _write_opencode_mcp(alice, "alice-oc")
            _write_opencode_mcp(bob, "bob-oc")

            real_path = Path

            class _PathShim:
                """Construct real Paths, but redirect the one hardcoded
                ``Path("/Users")`` literal to the temp tree and report
                ``home() == alice`` so relative-path resolution stays inside it."""

                def __new__(cls, *args, **kwargs):
                    if len(args) == 1 and args[0] == "/Users":
                        return real_path(users)
                    return real_path(*args, **kwargs)

                @staticmethod
                def home():
                    return alice

            with mock.patch.object(oc_macos, "Path", _PathShim), \
                 mock.patch(
                     "scripts.coding_discovery_tools.macos_extraction_helpers.is_running_as_root",
                     return_value=True,
                 ):
                configs = oc_macos.extract_opencode_global_mcp_config_with_root_support(
                    alice / ".config" / "opencode" / "opencode.json",
                    tool_name="OpenCode",
                    parent_levels=3,
                )

        self.assertEqual(len(configs), 2)
        self.assertEqual(_server_names(configs), {"alice-oc", "bob-oc"})


class TestOpenCodeWindowsMultiUserRoot(unittest.TestCase):
    r"""Windows OpenCode has its OWN copy of the helper and is the only
    STRUCTURALLY-DIVERGENT fixed file: it uses an inline, module-local
    ``_is_running_as_admin()`` (not ``_iter_admin_user_homes`` / not
    ``is_running_as_root``), hardcodes ``users_dir = Path("C:\Users")``, declares
    ``configs``/``seen_paths`` at the TOP of the function, and collapses BOTH the
    admin and non-admin paths into a SINGLE unified ``if not configs:`` fallback.

    It is behaviorally equivalent to the macOS/Cursor/Codex variants but was NOT
    directly tested, and it is the highest-risk file: a future edit could silently
    turn its ``if not configs`` fallback into an always-add with no test catching
    it. These cases lock that unified-fallback contract in.

    Patch approach: the helper imports ``ctypes`` LAZILY (inside
    ``_is_running_as_admin``), so the module loads fine on this Darwin host and we
    drive the REAL Windows helper. We force admin on by patching the exact
    module-local symbol the helper calls (``oc_windows._is_running_as_admin``),
    and redirect the one hardcoded ``Path("C:\Users")`` literal (plus
    ``Path.home()``) to a temp users tree via a thin ``_PathShim`` on the module's
    ``Path`` symbol — mirroring the macOS OpenCode test's shim. The
    ``global_config_path`` argument stays a real ``Path``, so ``.relative_to``,
    ``.exists`` and ``.read_text`` use genuine filesystem behavior.
    """

    @staticmethod
    def _make_path_shim(users_root: Path, home_dir: Path):
        r"""Build a ``Path`` stand-in: redirect the hardcoded ``Path("C:\Users")``
        literal to ``users_root`` and report ``home() == home_dir``; delegate
        every other construction to the real ``pathlib.Path``."""
        real_path = Path

        class _PathShim:
            def __new__(cls, *args, **kwargs):
                if len(args) == 1 and args[0] == "C:\\Users":
                    return real_path(users_root)
                return real_path(*args, **kwargs)

            @staticmethod
            def home():
                return home_dir

        return _PathShim

    def _run(self, *, global_config_path, users_root, home_dir, is_admin):
        """Drive the real Windows helper with ``_is_running_as_admin`` forced to
        ``is_admin`` and ``C:\\Users``/``home()`` redirected into the temp tree."""
        shim = self._make_path_shim(users_root, home_dir)
        with mock.patch.object(oc_windows, "Path", shim), \
             mock.patch.object(
                 oc_windows, "_is_running_as_admin", return_value=is_admin
             ):
            return oc_windows.extract_opencode_global_mcp_config_with_root_support(
                global_config_path,
                tool_name="OpenCode",
                parent_levels=5,  # AppData\Roaming\.config\opencode\opencode.json -> home
            )

    @staticmethod
    def _oc_config_path(home: Path) -> Path:
        r"""The real Windows global-config path under a given home:
        ``<home>\AppData\Roaming\.config\opencode\opencode.json``."""
        return home / "AppData" / "Roaming" / ".config" / "opencode" / "opencode.json"

    def test_both_users_servers_recovered_opencode_windows(self):
        """Assertion 1 (the regression): an admin scan accumulates BOTH users'
        servers. Pre-fix, the per-loop ``return config`` dropped everyone after
        the first; only one user's server survived."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            bob = users / "bob"
            _write_opencode_mcp_windows(alice, "alice-oc-win")
            _write_opencode_mcp_windows(bob, "bob-oc-win")

            configs = self._run(
                global_config_path=self._oc_config_path(alice),
                users_root=users,
                home_dir=alice,  # relative-path resolution anchor
                is_admin=True,
            )

        self.assertEqual(len(configs), 2, "both users' configs must be collected")
        self.assertEqual(_server_names(configs), {"alice-oc-win", "bob-oc-win"})
        # Distinct per-user homes are preserved as the config ``path`` keys.
        self.assertEqual({c["path"] for c in configs}, {str(alice), str(bob)})

    def test_single_user_non_admin_yields_one_element_list_opencode_windows(self):
        """Assertion 2: the non-admin path is unchanged — exactly one config,
        content-identical to the pre-fix single dict (the common case)."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"
            _write_opencode_mcp_windows(home, "solo-oc-win")

            configs = self._run(
                global_config_path=self._oc_config_path(home),
                users_root=Path(td) / "Users",
                home_dir=home,
                is_admin=False,  # non-admin: the C:\Users walk is skipped entirely
            )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(home))
        self.assertEqual(_server_names(configs), {"solo-oc-win"})

    def test_non_admin_missing_config_yields_empty_list_opencode_windows(self):
        """Non-admin with no config file present -> empty list (unchanged)."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "Users" / "solo"  # no opencode.json written
            home.mkdir(parents=True)

            configs = self._run(
                global_config_path=self._oc_config_path(home),
                users_root=Path(td) / "Users",
                home_dir=home,
                is_admin=False,
            )

        self.assertEqual(configs, [])

    def test_root_fallback_to_admin_own_home_when_no_user_config_opencode_windows(self):
        r"""Assertion 3a: admin homes exist but NONE has an OpenCode config, so the
        unified ``if not configs:`` fallback supplies the admin's OWN
        ``global_config_path`` — exactly one config, matching original
        single-user-root behavior.

        The admin's home is kept OUTSIDE the scanned ``C:\Users`` tree so the only
        way ``admin-oc-win`` can surface is the post-loop fallback, never the
        per-user pickup."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"            # scanned tree: only empty homes
            (users / "empty_a").mkdir(parents=True)
            (users / "empty_b").mkdir(parents=True)
            admin_home = Path(td) / "admin_home"  # NOT under the scanned tree
            _write_opencode_mcp_windows(admin_home, "admin-oc-win")  # admin's OWN

            configs = self._run(
                global_config_path=self._oc_config_path(admin_home),
                users_root=users,
                home_dir=admin_home,
                is_admin=True,
            )

        self.assertEqual(len(configs), 1)
        self.assertEqual(_server_names(configs), {"admin-oc-win"})

    def test_root_fallback_suppressed_when_user_config_present_opencode_windows(self):
        """Assertion 3b (the specific thing the unified ``if not configs`` must get
        right): once ANY per-user config is found, the fallback is suppressed, so
        the admin's own home is never double-counted alongside real user configs.
        A regression turning the fallback into an always-add would fail HERE."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            _write_opencode_mcp_windows(alice, "alice-oc-win")       # real per-user
            admin_home = Path(td) / "admin_home"                     # outside tree
            _write_opencode_mcp_windows(admin_home, "admin-oc-win")  # admin's OWN

            configs = self._run(
                global_config_path=self._oc_config_path(admin_home),
                users_root=users,
                home_dir=admin_home,
                is_admin=True,
            )

        # Only the real per-user config; admin's own home is the fallback and is
        # suppressed because alice's config was found.
        self.assertEqual(_server_names(configs), {"alice-oc-win"})

    def test_dedup_collapses_same_path_seen_twice_opencode_windows(self):
        r"""Assertion 4: de-dup by ``path`` (the Windows admin-own-home
        double-count case) collapses the same user dir surfacing twice into a
        single config — exercising the helper's ``seen_paths`` guard.

        A ``_DupIterUsers`` stands in for ``users_dir`` so ``iterdir`` lists alice
        twice while ``exists`` defers to the real temp tree (the helper calls only
        ``users_dir.exists()`` then ``users_dir.iterdir()``)."""
        with tempfile.TemporaryDirectory() as td:
            users = Path(td) / "Users"
            alice = users / "alice"
            alice_dup = users / "alice"  # same path, listed again
            _write_opencode_mcp_windows(alice, "alice-oc-win")

            class _DupIterUsers:
                def exists(self_inner):
                    return users.exists()

                def iterdir(self_inner):
                    return iter([alice, alice_dup])

            # Redirect the hardcoded Path("C:\Users") literal to the duplicating
            # stand-in; home() still anchors relative-path resolution at alice.
            real_path = Path

            class _DupPathShim:
                def __new__(cls, *args, **kwargs):
                    if len(args) == 1 and args[0] == "C:\\Users":
                        return _DupIterUsers()
                    return real_path(*args, **kwargs)

                @staticmethod
                def home():
                    return alice

            with mock.patch.object(oc_windows, "Path", _DupPathShim), \
                 mock.patch.object(
                     oc_windows, "_is_running_as_admin", return_value=True
                 ):
                configs = oc_windows.extract_opencode_global_mcp_config_with_root_support(
                    self._oc_config_path(alice),
                    tool_name="OpenCode",
                    parent_levels=5,
                )

        self.assertEqual(len(configs), 1, "duplicate path must collapse to one")
        self.assertEqual(_server_names(configs), {"alice-oc-win"})


if __name__ == "__main__":
    unittest.main()
