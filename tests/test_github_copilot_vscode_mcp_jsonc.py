"""
JSONC + dead-fallback tests for the GitHub Copilot VS Code MCP extractors.

The VS Code ``Code/User/mcp.json`` is JSONC in practice (VS Code lets users add
// and /* */ comments and trailing commas). The github_copilot extractors strip
JSONC comments and trailing commas before ``json.loads``; without that, any
commented or trailing-comma config raises JSONDecodeError and surfaces ZERO
servers. They reuse the same strippers the Copilot CLI extractor uses.

The dead ``globalStorage/ms-vscode.vscode-github-copilot/mcp.json`` fallback
branch was removed. VS Code Copilot only ever reads the primary
``Code/User/mcp.json``, so a server-bearing file at the old globalStorage path
must NOT be consulted.

These exercise the per-user method ``_extract_vscode_configs_for_user`` directly
(passing a temp home) to avoid full-filesystem scans. A shared mixin runs every
case against all three OS extractors, each with its correct per-OS Code/User
base path.

Conventions mirror the existing suite: temp HOME dirs, the globally-stubbed MCP
scanner (``tests/__init__.py`` patches ``_scan_servers_in_mapping`` -> {}), and
``_SENTRY_DSN`` forced empty to prevent real Sentry calls.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.mcp_extraction_helpers as mcp_helpers
from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    _normalize_vscode_cached_launch,
    _read_vscode_state_json,
    _transform_vscode_cached_mcp_servers,
    _vscode_cached_url_for_report,
    enumerate_vscode_mcp_files,
)
from scripts.coding_discovery_tools.macos.github_copilot.mcp_config_extractor import (
    MacOSGitHubCopilotMCPConfigExtractor,
)
from scripts.coding_discovery_tools.linux.github_copilot.mcp_config_extractor import (
    LinuxGitHubCopilotMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.github_copilot.mcp_config_extractor import (
    WindowsGitHubCopilotMCPConfigExtractor,
)


class _GitHubCopilotVscodeMcpMixin:
    """Shared cases parametrized over the 3 OS extractors.

    Subclasses set ``extractor_cls`` and ``code_user_relpath`` (the per-OS path
    from the user home down to the ``Code/User`` dir that holds ``mcp.json``).
    """

    extractor_cls = None
    code_user_relpath = ()  # tuple of path segments under user_home

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = self.extractor_cls()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.code_user_base = self.user_home.joinpath(*self.code_user_relpath)
        self.code_user_base.mkdir(parents=True)
        self.primary_path = self.code_user_base / "mcp.json"
        # Insiders base is a sibling of the stable base with the trailing
        # "Code" segment swapped for "Code - Insiders": code_user_base ends in
        # ``.../Code/User`` so parent.parent strips ``User`` then ``Code``.
        self.insiders_base = (
            self.code_user_base.parent.parent / "Code - Insiders" / "User"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_primary(self, text: str) -> None:
        self.primary_path.write_text(text, encoding="utf-8")

    def _extract(self):
        return self.extractor._extract_vscode_configs_for_user(self.user_home, ["Code"])

    def _install_gitlens_provider(self, declares_provider: bool = True) -> Path:
        extension_dir = (
            self.user_home
            / ".vscode"
            / "extensions"
            / "eamodio.gitlens-17.10.0"
        )
        extension_dir.mkdir(parents=True, exist_ok=True)
        providers = (
            [{"id": "gitlens.gkMcpProvider", "label": "GitKraken"}]
            if declares_provider
            else []
        )
        (extension_dir / "package.json").write_text(
            json.dumps(
                {
                    "publisher": "eamodio",
                    "name": "gitlens",
                    "contributes": {"mcpServerDefinitionProviders": providers},
                }
            ),
            encoding="utf-8",
        )
        (extension_dir.parent / "extensions.json").write_text(
            json.dumps(
                [
                    {
                        "identifier": {"id": "eamodio.gitlens"},
                        "version": "17.10.0",
                        "relativeLocation": extension_dir.name,
                    }
                ]
            ),
            encoding="utf-8",
        )
        return extension_dir

    def _write_gitkraken_provider_cache(
        self,
        workspace_id: str = "workspace-a",
        create_command: bool = True,
        command_name: str = "gk.exe",
        workspace_uri: str = None,
        profile_id: str = None,
    ) -> Path:
        storage_base = self.code_user_base
        if profile_id is not None:
            storage_base = storage_base / "profiles" / profile_id
        db_path = storage_base / "workspaceStorage" / workspace_id / "state.vscdb"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if workspace_uri is None:
            workspace_uri = self._workspace_location(workspace_id)[0]
        (db_path.parent / "workspace.json").write_text(
            json.dumps({"folder": workspace_uri}),
            encoding="utf-8",
        )
        command = (
            self.code_user_base
            / "globalStorage"
            / "eamodio.gitlens"
            / command_name
        )
        if create_command:
            command.parent.mkdir(parents=True, exist_ok=True)
            command.touch()
        cached = {
            "eamodio.gitlens/gitlens.gkMcpProvider": {
                "servers": [
                    {
                        "id": "eamodio.gitlens/GitKraken",
                        "label": "GitKraken",
                        "cacheNonce": "3.1.70",
                        "launch": {
                            "type": 1,
                            "cwd": str(self.code_user_base / "globalStorage" / "eamodio.gitlens"),
                            "command": str(command),
                            "args": [
                                "mcp",
                                "--host=vscode",
                                "--source=gitlens",
                                "--scheme=vscode",
                            ],
                            "env": {"SHOULD_NOT_LEAVE_DEVICE": "secret"},
                        },
                    }
                ]
            }
        }
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("mcp.extCachedServers", json.dumps(cached)),
            )
            conn.commit()
        return db_path

    def _workspace_location(self, workspace_id: str):
        workspace_path = self.user_home / workspace_id
        workspace_uri = workspace_path.absolute().as_uri()
        return (
            workspace_uri,
            mcp_helpers._vscode_workspace_uri_for_report(
                workspace_uri,
                self.operating_system,
            ),
        )

    def _write_extension_state(self, db_path: Path, key: str, extension_id: str) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            conn.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (key, json.dumps([{"id": extension_id}])),
            )
            conn.commit()

    @staticmethod
    def _all_servers(configs):
        return [
            server
            for config in configs
            for server in config.get("mcpServers", [])
        ]

    def _server_names(self, configs) -> set:
        self.assertEqual(len(configs), 1)
        return {s["name"] for s in configs[0]["mcpServers"]}

    # -- JSONC tolerance ---------------------------------------------------

    def test_commented_mcp_json_surfaces_server(self):
        """// and /* */ comments must be stripped (previously surfaced 0 servers)."""
        self._write_primary(
            "{\n"
            "  // VS Code Copilot MCP servers\n"
            '  "servers": {\n'
            '    "serena": { "command": "uvx", "args": ["serena"] } /* serena */\n'
            "  }\n"
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

    def test_trailing_comma_mcp_json_surfaces_server(self):
        """A hand-edited trailing comma must parse (previously surfaced 0 servers)."""
        self._write_primary('{"servers":{"serena":{"command":"uvx"}},}')
        self.assertIn("serena", self._server_names(self._extract()))

    def test_comment_and_trailing_comma_combined_surfaces_server(self):
        """Comments AND a trailing comma together (the real-world hand-edit)."""
        self._write_primary(
            "{\n"
            '  "servers": {\n'
            '    "serena": { "command": "uvx" }, // serena\n'
            "  },\n"
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

    def test_valid_clean_json_still_surfaces(self):
        """No regression: a plain valid JSON config still surfaces the server."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self.assertIn("serena", self._server_names(self._extract()))

    def test_extension_provider_cache_surfaces_gitkraken(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache()

        configs = self._extract()
        servers = self._all_servers(configs)
        self.assertEqual(len(servers), 1)
        self.assertEqual(configs[0]["path"], self._workspace_location("workspace-a")[1])
        server = servers[0]
        self.assertEqual(server["name"], "GitKraken")
        self.assertEqual(server["type"], "stdio")
        self.assertTrue(server["command"].endswith("gk.exe"))
        self.assertEqual(server["args"][0], "mcp")
        self.assertEqual(
            server["providerId"],
            "eamodio.gitlens/gitlens.gkMcpProvider",
        )
        self.assertEqual(server["providerServerId"], "eamodio.gitlens/GitKraken")
        self.assertEqual(
            server["additional_data"],
            {"scope": "vscode-provider-cache"},
        )
        self.assertNotIn("providerCacheNonce", server)
        self.assertNotIn("env", server)

    def test_static_and_extension_provider_servers_are_both_preserved(self):
        self._write_primary(
            json.dumps({"servers": {"postman": {"url": "https://mcp.postman.com/minimal"}}})
        )
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache()

        names = {server["name"] for server in self._all_servers(self._extract())}
        self.assertEqual(names, {"postman", "GitKraken"})

    def test_extension_provider_cache_preserves_workspace_scope(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache("workspace-a")
        self._write_gitkraken_provider_cache("workspace-b")

        configs = self._extract()
        self.assertEqual(len(configs), 2)
        self.assertEqual(
            {config["path"] for config in configs},
            {
                self._workspace_location("workspace-a")[1],
                self._workspace_location("workspace-b")[1],
            },
        )
        self.assertEqual(
            [server["name"] for server in self._all_servers(configs)],
            ["GitKraken", "GitKraken"],
        )

    def test_workspace_scope_path_is_normalized_before_reporting(self):
        self._install_gitlens_provider()
        workspace = self.user_home / "workspace"
        workspace.mkdir()
        workspace_uri = (
            self.user_home / "placeholder" / ".." / "workspace"
        ).absolute().as_uri()
        self._write_gitkraken_provider_cache(
            "workspace-normalized",
            workspace_uri=workspace_uri,
        )

        configs = self._extract()

        expected = mcp_helpers._vscode_workspace_uri_for_report(
            workspace.absolute().as_uri(),
            self.operating_system,
        )
        self.assertEqual([config["path"] for config in configs], [expected])

    def test_extension_provider_without_optional_cache_nonce_is_preserved(self):
        self._install_gitlens_provider()
        db_path = self._write_gitkraken_provider_cache()
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("mcp.extCachedServers",),
            ).fetchone()
            cached = json.loads(row[0])
            provider = cached["eamodio.gitlens/gitlens.gkMcpProvider"]
            provider["servers"][0].pop("cacheNonce")
            conn.execute(
                "UPDATE ItemTable SET value = ? WHERE key = ?",
                (json.dumps(cached), "mcp.extCachedServers"),
            )
            conn.commit()

        servers = self._all_servers(self._extract())

        self.assertEqual([server["name"] for server in servers], ["GitKraken"])

    def test_newest_usable_cache_wins_for_stable_provider_identity(self):
        self._install_gitlens_provider()
        workspace_uri = self._workspace_location("shared-workspace")[0]
        old_db = self._write_gitkraken_provider_cache(
            "workspace-old",
            command_name="gk-old.exe",
            workspace_uri=workspace_uri,
        )
        new_db = self._write_gitkraken_provider_cache(
            "workspace-new",
            command_name="gk-new.exe",
            workspace_uri=workspace_uri,
        )
        os.utime(old_db, (1, 1))
        os.utime(new_db, (2, 2))

        servers = self._all_servers(self._extract())

        self.assertEqual(len(servers), 1)
        self.assertTrue(servers[0]["command"].endswith("gk-new.exe"))

    def test_same_workspace_preserves_distinct_profile_definitions(self):
        self._install_gitlens_provider()
        workspace_uri = self._workspace_location("shared-workspace")[0]
        self._write_gitkraken_provider_cache(
            "workspace-a",
            command_name="gk-a.exe",
            workspace_uri=workspace_uri,
            profile_id="profile-a",
        )
        self._write_gitkraken_provider_cache(
            "workspace-b",
            command_name="gk-b.exe",
            workspace_uri=workspace_uri,
            profile_id="profile-b",
        )

        configs = self._extract()
        servers = self._all_servers(configs)
        self.assertEqual(len(configs), 1)
        self.assertEqual(len(servers), 2)
        self.assertEqual(
            {server["providerProfileId"] for server in servers},
            {"profile-a", "profile-b"},
        )

    def test_workspace_cache_without_scope_metadata_is_ignored(self):
        self._install_gitlens_provider()
        db_path = self._write_gitkraken_provider_cache()
        (db_path.parent / "workspace.json").unlink()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_symlinked_workspace_cache_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        self._install_gitlens_provider()
        db_path = self._write_gitkraken_provider_cache()
        external_db = Path(self.tmp_dir) / "external-state.vscdb"
        db_path.replace(external_db)
        db_path.symlink_to(external_db)

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_symlinked_workspace_storage_directory_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        self._install_gitlens_provider()
        db_path = self._write_gitkraken_provider_cache()
        workspace_dir = db_path.parent
        external_workspace = Path(self.tmp_dir) / "external-workspace"
        workspace_dir.replace(external_workspace)
        workspace_dir.symlink_to(external_workspace, target_is_directory=True)

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_symlinked_workspace_metadata_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        self._install_gitlens_provider()
        db_path = self._write_gitkraken_provider_cache()
        metadata_path = db_path.parent / "workspace.json"
        external_metadata = Path(self.tmp_dir) / "external-workspace.json"
        metadata_path.replace(external_metadata)
        metadata_path.symlink_to(external_metadata)

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_symlinked_profile_state_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache(profile_id="profile-a")
        profile_state = (
            self.code_user_base
            / "profiles"
            / "profile-a"
            / "globalStorage"
            / "state.vscdb"
        )
        external_state = Path(self.tmp_dir) / "external-profile-state.vscdb"
        self._write_extension_state(
            external_state,
            "extensionsIdentifiers/disabled",
            "eamodio.gitlens",
        )
        profile_state.parent.mkdir(parents=True, exist_ok=True)
        profile_state.symlink_to(external_state)

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_elevated_discovery_reports_cache_without_executing_it(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache()

        with patch.object(
            mcp_helpers,
            "_running_with_elevated_privileges",
            return_value=True,
        ), patch.object(
            mcp_helpers,
            "_vscode_cached_command_exists",
        ) as command_exists, patch.object(
            mcp_helpers,
            "_scan_servers_in_mapping",
        ) as scan_servers, patch.object(
            mcp_helpers,
            "augment_script_fields",
        ) as augment_script:
            servers = self._all_servers(self._extract())

        command_exists.assert_not_called()
        scan_servers.assert_not_called()
        augment_script.assert_not_called()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["scan"]["error"]["code"], "privilege_boundary")

    def test_non_elevated_discovery_scans_cached_launch(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache()
        scan = {
            "scanned_at": None,
            "tools": [{"name": "repository_status"}],
            "tool_count": 1,
            "server_info": None,
            "error": None,
        }

        with patch.object(
            mcp_helpers,
            "_running_with_elevated_privileges",
            return_value=False,
        ), patch.object(
            mcp_helpers,
            "_scan_servers_in_mapping",
            return_value={"0": scan},
        ) as scan_servers:
            servers = self._all_servers(self._extract())

        scanned_config = scan_servers.call_args.args[0]["0"]
        self.assertEqual(scanned_config["env"], {"SHOULD_NOT_LEAVE_DEVICE": "secret"})
        self.assertEqual(servers[0]["scan"]["tool_count"], 1)
        self.assertNotIn("env", servers[0])

    def test_uninstalled_extension_cache_is_ignored(self):
        self._write_gitkraken_provider_cache()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_extension_manifest_outside_registry_root_is_ignored(self):
        extension_dir = self._install_gitlens_provider()
        outside_dir = Path(self.tmp_dir) / "outside-extension"
        outside_dir.mkdir()
        shutil.copy(extension_dir / "package.json", outside_dir / "package.json")
        (extension_dir.parent / "extensions.json").write_text(
            json.dumps([
                {
                    "identifier": {"id": "eamodio.gitlens"},
                    "location": {"fsPath": str(outside_dir)},
                }
            ]),
            encoding="utf-8",
        )
        self._write_gitkraken_provider_cache()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_symlinked_extension_directory_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        extension_dir = self._install_gitlens_provider()
        external_extension = Path(self.tmp_dir) / "external-extension"
        extension_dir.replace(external_extension)
        extension_dir.symlink_to(external_extension, target_is_directory=True)
        self._write_gitkraken_provider_cache()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_workspace_cache_cannot_claim_another_user(self):
        self._install_gitlens_provider()
        if self.operating_system == "windows":
            workspace_uri = "file:///C:/Users/other/project"
        elif self.operating_system == "macos":
            workspace_uri = "file:///Users/other/project"
        else:
            workspace_uri = "file:///home/other/project"
        self._write_gitkraken_provider_cache(workspace_uri=workspace_uri)

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_provider_removed_from_live_manifest_is_ignored(self):
        self._install_gitlens_provider(declares_provider=False)
        self._write_gitkraken_provider_cache()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_oversized_extension_manifest_is_ignored(self):
        extension_dir = self._install_gitlens_provider()
        (extension_dir / "package.json").write_bytes(
            b" " * (mcp_helpers._VSCODE_EXTENSION_MANIFEST_MAX_BYTES + 1)
        )
        self._write_gitkraken_provider_cache()

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_globally_disabled_extension_cache_is_ignored(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache()
        self._write_extension_state(
            self.code_user_base / "globalStorage" / "state.vscdb",
            "extensionsIdentifiers/disabled",
            "eamodio.gitlens",
        )

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_workspace_disabled_extension_cache_is_ignored(self):
        self._install_gitlens_provider()
        workspace_db = self._write_gitkraken_provider_cache()
        self._write_extension_state(
            workspace_db,
            "extensionsIdentifiers/disabled",
            "eamodio.gitlens",
        )

        self.assertEqual(self._all_servers(self._extract()), [])

    def test_workspace_enablement_overrides_global_disablement(self):
        self._install_gitlens_provider()
        workspace_db = self._write_gitkraken_provider_cache()
        self._write_extension_state(
            self.code_user_base / "globalStorage" / "state.vscdb",
            "extensionsIdentifiers/disabled",
            "eamodio.gitlens",
        )
        self._write_extension_state(
            workspace_db,
            "extensionsIdentifiers/enabled",
            "eamodio.gitlens",
        )

        servers = self._all_servers(self._extract())
        self.assertEqual([server["name"] for server in servers], ["GitKraken"])

    def test_missing_cached_executable_is_ignored(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache(create_command=False)

        with patch.object(
            mcp_helpers,
            "_running_with_elevated_privileges",
            return_value=False,
        ):
            self.assertEqual(self._all_servers(self._extract()), [])

    def test_relative_cached_executable_is_resolved_from_cwd(self):
        self._install_gitlens_provider()
        self._write_gitkraken_provider_cache(command_name="gk.exe")
        db_path = (
            self.code_user_base
            / "workspaceStorage"
            / "workspace-a"
            / "state.vscdb"
        )
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("mcp.extCachedServers",),
            ).fetchone()
            cached = json.loads(row[0])
            provider = cached["eamodio.gitlens/gitlens.gkMcpProvider"]
            launch = provider["servers"][0]["launch"]
            launch["command"] = "gk.exe"
            conn.execute(
                "UPDATE ItemTable SET value = ? WHERE key = ?",
                (json.dumps(cached), "mcp.extCachedServers"),
            )
            conn.commit()

        with patch.object(
            mcp_helpers,
            "_running_with_elevated_privileges",
            return_value=False,
        ):
            servers = self._all_servers(self._extract())

        self.assertEqual([server["name"] for server in servers], ["GitKraken"])

    def test_builtin_extension_provider_is_accepted(self):
        extension_root = self.user_home / "vscode-builtins"
        extension_dir = extension_root / "gitlens"
        extension_dir.mkdir(parents=True)
        (extension_dir / "package.json").write_text(
            json.dumps(
                {
                    "publisher": "eamodio",
                    "name": "gitlens",
                    "contributes": {
                        "mcpServerDefinitionProviders": [
                            {"id": "gitlens.gkMcpProvider", "label": "GitKraken"}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        self._write_gitkraken_provider_cache()

        with patch.object(
            mcp_helpers,
            "_vscode_builtin_extension_roots",
            return_value=[extension_root],
        ):
            servers = self._all_servers(self._extract())

        self.assertEqual([server["name"] for server in servers], ["GitKraken"])

    def test_symlinked_builtin_extension_directory_is_ignored(self):
        if os.name == "nt":
            self.skipTest("Windows test runners may not permit symlink creation")
        extension_root = self.user_home / "vscode-builtins"
        external_extension = Path(self.tmp_dir) / "external-builtin"
        external_extension.mkdir()
        (external_extension / "package.json").write_text(
            json.dumps(
                {
                    "publisher": "eamodio",
                    "name": "gitlens",
                    "contributes": {
                        "mcpServerDefinitionProviders": [
                            {"id": "gitlens.gkMcpProvider"}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        extension_root.mkdir()
        (extension_root / "gitlens").symlink_to(
            external_extension,
            target_is_directory=True,
        )
        self._write_gitkraken_provider_cache()

        with patch.object(
            mcp_helpers,
            "_vscode_builtin_extension_roots",
            return_value=[extension_root],
        ):
            servers = self._all_servers(self._extract())

        self.assertEqual(servers, [])

    def test_corrupt_extension_provider_cache_does_not_hide_static_config(self):
        self._write_primary(
            json.dumps({"servers": {"figma": {"url": "https://mcp.figma.com/mcp"}}})
        )
        db_path = (
            self.code_user_base
            / "workspaceStorage"
            / "broken"
            / "state.vscdb"
        )
        db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("mcp.extCachedServers", "{broken-json"),
            )
            conn.commit()

        names = {server["name"] for server in self._all_servers(self._extract())}
        self.assertEqual(names, {"figma"})

    def test_both_top_level_keys_resolve_under_stripping(self):
        """Both ``servers`` and ``mcpServers`` top-level keys resolve, with JSONC."""
        # ``servers`` form (VS Code style) with a comment.
        self._write_primary(
            "{\n"
            '  "servers": { "serena": { "command": "uvx" } } // vscode style\n'
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

        # ``mcpServers`` form (Claude style) with a trailing comma.
        self._write_primary('{"mcpServers":{"github":{"url":"https://x/mcp"}},}')
        self.assertIn("github", self._server_names(self._extract()))

    # -- Primary path read; dead fallback removed --------------------------

    def test_primary_only_surfaces_and_returns_code_user_base(self):
        """Only the primary Code/User/mcp.json present → servers surface and the
        returned path is the Code/User base."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertIn("serena", self._server_names(configs))
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_old_globalstorage_fallback_never_consulted(self):
        """A server-bearing file at the OLD globalStorage path with NO primary
        must yield an empty result — the dead fallback branch is gone."""
        fallback_path = (
            self.code_user_base
            / "globalStorage"
            / "ms-vscode.vscode-github-copilot"
            / "mcp.json"
        )
        fallback_path.parent.mkdir(parents=True)
        fallback_path.write_text(
            json.dumps({"servers": {"serena": {"command": "uvx"}}}),
            encoding="utf-8",
        )
        self.assertEqual(self._extract(), [])

    # -- Customer-machine safety -------------------------------------------

    def test_irreparably_malformed_json_no_crash_empty(self):
        """JSON broken beyond comments/commas must not raise; returns empty."""
        self._write_primary("{ this is not valid json {{{")
        self.assertEqual(self._extract(), [])

    # -- Named profiles + Insiders (WEB-4703 fix #2, A+B) ------------------

    def _write_profile(self, base: Path, profile_id: str, servers: dict) -> None:
        """Write a profile-scoped mcp.json under ``base/profiles/<id>/``."""
        profile_dir = base / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "mcp.json").write_text(
            json.dumps({"servers": servers}), encoding="utf-8"
        )

    def _configs_by_path(self, configs) -> dict:
        """Map each returned config's path -> set of its server names."""
        return {
            cfg["path"]: {s["name"] for s in cfg["mcpServers"]}
            for cfg in configs
        }

    def test_default_only_unchanged_single_config_and_path(self):
        """Only the default mcp.json ⇒ exactly one config at the base path."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))
        self.assertIn("serena", {s["name"] for s in configs[0]["mcpServers"]})

    def test_named_profile_surfaces_with_profile_dir_path(self):
        """Default + one named profile ⇒ two configs; the profile config is
        attributed to its own ``profiles/<id>`` dir."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self._write_profile(self.code_user_base, "abc123", {"github": {"url": "https://x/mcp"}})

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(len(by_path), 2)
        self.assertEqual(by_path[str(self.code_user_base)], {"serena"})
        profile_path = str(self.code_user_base / "profiles" / "abc123")
        self.assertIn(profile_path, by_path)
        self.assertEqual(by_path[profile_path], {"github"})

    def test_multiple_profiles_each_separate_config(self):
        """Two named profiles each surface as a separate config keyed by their
        own profile-dir path (order-independent comparison)."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self._write_profile(self.code_user_base, "p1", {"alpha": {"command": "a"}})
        self._write_profile(self.code_user_base, "p2", {"beta": {"command": "b"}})

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(
            by_path,
            {
                str(self.code_user_base): {"serena"},
                str(self.code_user_base / "profiles" / "p1"): {"alpha"},
                str(self.code_user_base / "profiles" / "p2"): {"beta"},
            },
        )

    def test_absent_profiles_dir_no_crash(self):
        """Default present, no profiles/ dir ⇒ no crash, single default config."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_profile_dir_exists_but_no_mcp_json_ignored(self):
        """An empty ``profiles/empty/`` dir is ignored; only the default surfaces."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        (self.code_user_base / "profiles" / "empty").mkdir(parents=True)
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_insiders_default_mcp_surfaces_with_insiders_path(self):
        """Default mcp.json under the Insiders base ONLY ⇒ one config attributed
        to the Insiders base."""
        self.insiders_base.mkdir(parents=True)
        (self.insiders_base / "mcp.json").write_text(
            json.dumps({"servers": {"serena": {"command": "uvx"}}}), encoding="utf-8"
        )
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.insiders_base))
        self.assertIn("serena", {s["name"] for s in configs[0]["mcpServers"]})

    def test_stable_and_insiders_both_surface(self):
        """Default mcp.json in BOTH stable and Insiders ⇒ two configs, each
        attributed to its own base path."""
        self._write_primary(json.dumps({"servers": {"stable_srv": {"command": "s"}}}))
        self.insiders_base.mkdir(parents=True)
        (self.insiders_base / "mcp.json").write_text(
            json.dumps({"servers": {"insiders_srv": {"command": "i"}}}), encoding="utf-8"
        )

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(
            by_path,
            {
                str(self.code_user_base): {"stable_srv"},
                str(self.insiders_base): {"insiders_srv"},
            },
        )

    def test_insiders_profile_surfaces(self):
        """A profile under the Insiders base surfaces with its Insiders
        profile-dir path."""
        self.insiders_base.mkdir(parents=True)
        self._write_profile(self.insiders_base, "x", {"gamma": {"command": "g"}})

        by_path = self._configs_by_path(self._extract())
        profile_path = str(self.insiders_base / "profiles" / "x")
        self.assertIn(profile_path, by_path)
        self.assertEqual(by_path[profile_path], {"gamma"})


class TestEnumerateVscodeMcpFiles(unittest.TestCase):
    """OS-agnostic contract tests for ``enumerate_vscode_mcp_files``."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.base = Path(self.tmp_dir) / "User"
        self.base.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_default(self) -> Path:
        default_file = self.base / "mcp.json"
        default_file.write_text("{}", encoding="utf-8")
        return default_file

    def _write_profile(self, profile_id: str) -> Path:
        profile_dir = self.base / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_file = profile_dir / "mcp.json"
        profile_file.write_text("{}", encoding="utf-8")
        return profile_file

    def test_default_only_returns_default_file(self):
        default_file = self._write_default()
        self.assertEqual(enumerate_vscode_mcp_files(self.base), [default_file])

    def test_default_plus_two_profiles_sorted_order(self):
        default_file = self._write_default()
        # Write out of order to prove sorting.
        p_b = self._write_profile("bbb")
        p_a = self._write_profile("aaa")
        self.assertEqual(
            enumerate_vscode_mcp_files(self.base),
            [default_file, p_a, p_b],
        )

    def test_nonexistent_base_returns_empty(self):
        missing = Path(self.tmp_dir) / "does_not_exist" / "User"
        self.assertEqual(enumerate_vscode_mcp_files(missing), [])

    def test_empty_profiles_dir_returns_just_default(self):
        default_file = self._write_default()
        (self.base / "profiles").mkdir()
        self.assertEqual(enumerate_vscode_mcp_files(self.base), [default_file])


class TestNormalizeVscodeCachedLaunch(unittest.TestCase):
    def test_http_uri_components_are_rebuilt(self):
        config = _normalize_vscode_cached_launch(
            {
                "type": 2,
                "uri": {
                    "scheme": "https",
                    "authority": "mcp.example.com",
                    "path": "/api/mcp",
                    "query": "mode=minimal",
                    "fragment": "",
                },
                "headers": [["Authorization", "Bearer secret"]],
            }
        )

        self.assertEqual(config["type"], "http")
        self.assertEqual(
            config["url"],
            "https://mcp.example.com/api/mcp?mode=minimal",
        )
        self.assertEqual(config["headers"], {"Authorization": "Bearer secret"})

    def test_unknown_transport_is_rejected(self):
        self.assertIsNone(
            _normalize_vscode_cached_launch(
                {"type": 99, "command": "node", "args": [], "env": {}}
            )
        )

    def test_report_url_drops_credentials_query_and_fragment(self):
        self.assertEqual(
            _vscode_cached_url_for_report(
                "https://alice:secret@mcp.example.com:8443/api/mcp?token=secret#private"
            ),
            "https://mcp.example.com:8443/api/mcp",
        )

    def test_state_value_limit_is_measured_in_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.vscdb"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
                conn.execute(
                    "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                    ("unicode", json.dumps("é", ensure_ascii=False)),
                )
                conn.commit()

            self.assertIsNone(_read_vscode_state_json(db_path, "unicode", 3))

    def test_cached_http_credentials_are_not_reported(self):
        scan = {
            "scanned_at": None,
            "tools": [],
            "tool_count": 0,
            "server_info": None,
            "error": None,
        }
        configs = {
            "0": {
                "type": "http",
                "url": "https://alice:secret@mcp.example.com/mcp?token=secret",
                "headers": {"Authorization": "Bearer secret"},
            }
        }
        with patch.object(
            mcp_helpers,
            "_scan_servers_in_mapping",
            return_value={"0": scan},
        ) as scan_servers:
            servers = _transform_vscode_cached_mcp_servers(
                configs,
                {"0": "Example"},
            )

        self.assertEqual(
            scan_servers.call_args.args[0]["0"]["headers"],
            {"Authorization": "Bearer secret"},
        )
        server = servers[0]
        self.assertEqual(server["url"], "https://mcp.example.com/mcp")
        self.assertNotIn("headers", server)


class TestMacOSGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = MacOSGitHubCopilotMCPConfigExtractor
    code_user_relpath = ("Library", "Application Support", "Code", "User")
    operating_system = "macos"


class TestLinuxGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = LinuxGitHubCopilotMCPConfigExtractor
    code_user_relpath = (".config", "Code", "User")
    operating_system = "linux"


class TestWindowsGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = WindowsGitHubCopilotMCPConfigExtractor
    code_user_relpath = ("AppData", "Roaming", "Code", "User")
    operating_system = "windows"


if __name__ == "__main__":
    unittest.main()
