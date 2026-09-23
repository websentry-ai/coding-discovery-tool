"""Symlink-trap and secret-redaction tests for the pi / Zed / OpenCode extractors.

Under a root/MDM scan every path inside a user's home is attacker-controlled by
that user. These tests pin that the three new extractors:
  1. refuse a project config file that is a symlink to a file outside the
     project (another user's file, /etc/passwd, ...), and
  2. redact string values of credential-looking keys in JSON/JSONC config before
     the content enters the payload.

Symlink cases are skipped on Windows, where creating them needs a privilege and
``O_NOFOLLOW`` is a no-op (realpath containment is the guard there).
"""

import os
import tempfile
import unittest
from pathlib import Path

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.opencode.opencode_rules_extractor import (
    MacOSOpenCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos.pi.pi_rules_extractor import MacOSPiRulesExtractor
from scripts.coding_discovery_tools.macos.zed.zed_rules_extractor import MacOSZedRulesExtractor
from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list
from scripts.coding_discovery_tools.rule_read_helpers import redact_secret_values

SECRET = "sk-ant-live-THIS-MUST-NOT-LEAK"
_NO_SYMLINK = os.name == "nt"


class TestRedactSecretValues(unittest.TestCase):
    def test_redacts_credential_keys_only(self):
        text = (
            '{\n'
            '  "provider": { "anthropic": { "options": { "apiKey": "%s", "baseURL": "https://gw" } } },\n'
            '  "mcp": { "hub": { "headers": { "Authorization": "Bearer %s" } } },\n'
            '  "max_tokens": 4096,\n'
            '  "token_budget": 1000,\n'
            '  "model": "anthropic/claude-sonnet-5"\n'
            '}\n'
        ) % (SECRET, SECRET)
        out = redact_secret_values(text)
        self.assertNotIn(SECRET, out)
        self.assertIn('"apiKey": "***REDACTED***"', out)
        self.assertIn('"Authorization": "***REDACTED***"', out)
        self.assertIn('"baseURL": "https://gw"', out)
        self.assertIn('"max_tokens": 4096', out)
        self.assertIn('"token_budget": 1000', out)
        self.assertIn('"model": "anthropic/claude-sonnet-5"', out)

    def test_env_and_headers_blocks_fully_redacted(self):
        text = (
            '{\n'
            '  "mcp": {\n'
            '    "db": { "type": "local", "command": ["npx", "db-mcp"],\n'
            '            "environment": { "DATABASE_URL": "postgres://u:%s@h/db", "DEBUG": "1" } },\n'
            '    "hub": { "type": "remote", "url": "https://hub",\n'
            '             "headers": { "X-Api-Token": "%s", "Accept": "application/json" } }\n'
            '  },\n'
            '  "context_servers": { "gh": { "command": "gh-mcp", "env": { "GH_PAT": "%s" } } },\n'
            '  "model": "m"\n'
            '}\n'
        ) % (SECRET, SECRET, SECRET)
        out = redact_secret_values(text)
        self.assertNotIn(SECRET, out)
        # Whole block redacted, key names preserved.
        self.assertIn('"DATABASE_URL": "***REDACTED***"', out)
        self.assertIn('"DEBUG": "***REDACTED***"', out)
        self.assertIn('"X-Api-Token": "***REDACTED***"', out)
        self.assertIn('"Accept": "***REDACTED***"', out)
        self.assertIn('"GH_PAT": "***REDACTED***"', out)
        # Outside the blocks, nothing else changes.
        self.assertIn('"command": ["npx", "db-mcp"]', out)
        self.assertIn('"url": "https://hub"', out)
        self.assertIn('"model": "m"', out)

    def test_unbalanced_block_does_not_crash(self):
        out = redact_secret_values('{ "env": { "A": "%s", "B": "x" ' % SECRET)
        self.assertNotIn(SECRET, out)

    def test_handles_escaped_quotes_and_jsonc(self):
        text = '// comment\n{ "api_key": "ab\\"cd", "SECRET_TOKEN": "x", }\n'
        out = redact_secret_values(text)
        self.assertEqual(out, '// comment\n{ "api_key": "***REDACTED***", "SECRET_TOKEN": "***REDACTED***", }\n')


class _Base(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        # "Another user's" file, outside the project.
        self.outside = self.root / "victim" / "secrets.txt"
        self.outside.parent.mkdir()
        self.outside.write_text("VICTIM-DATA-" + SECRET, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _all_content(self, projects):
        return "\n".join(r["content"] for p in projects for r in p["rules"])


class TestPiContainedReads(_Base):
    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_settings_refused(self):
        pi_dir = self.proj / ".pi"
        pi_dir.mkdir()
        os.symlink(self.outside, pi_dir / "settings.json")
        projects = {}
        MacOSPiRulesExtractor()._extract_rules_from_pi_directory(pi_dir, projects)
        self.assertNotIn(SECRET, self._all_content(build_project_list(projects)))

    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_sibling_refused_but_real_settings_kept(self):
        pi_dir = self.proj / ".pi"
        pi_dir.mkdir()
        (pi_dir / "settings.json").write_text('{"model": "x"}', encoding="utf-8")
        os.symlink(self.outside, pi_dir / "SYSTEM.md")
        projects = {}
        MacOSPiRulesExtractor()._extract_rules_from_pi_directory(pi_dir, projects)
        result = build_project_list(projects)
        self.assertEqual([r["file_name"] for r in result[0]["rules"]], ["settings.json"])
        self.assertNotIn(SECRET, self._all_content(result))

    def test_api_key_in_settings_redacted(self):
        pi_dir = self.proj / ".pi"
        pi_dir.mkdir()
        (pi_dir / "settings.json").write_text('{"apiKey": "%s", "model": "m"}' % SECRET, encoding="utf-8")
        projects = {}
        MacOSPiRulesExtractor()._extract_rules_from_pi_directory(pi_dir, projects)
        content = self._all_content(build_project_list(projects))
        self.assertNotIn(SECRET, content)
        self.assertIn('"model": "m"', content)


class TestZedContainedReads(_Base):
    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_settings_refused(self):
        zed_dir = self.proj / ".zed"
        zed_dir.mkdir()
        os.symlink(self.outside, zed_dir / "settings.json")
        projects = {}
        MacOSZedRulesExtractor()._extract_rules_from_zed_directory(zed_dir, projects)
        self.assertNotIn(SECRET, self._all_content(build_project_list(projects)))

    def test_api_key_in_settings_redacted(self):
        zed_dir = self.proj / ".zed"
        zed_dir.mkdir()
        (zed_dir / "settings.json").write_text(
            '// zed\n{ "language_models": { "openai": { "api_key": "%s", "api_url": "https://gw" } }, }\n' % SECRET,
            encoding="utf-8",
        )
        projects = {}
        MacOSZedRulesExtractor()._extract_rules_from_zed_directory(zed_dir, projects)
        content = self._all_content(build_project_list(projects))
        self.assertNotIn(SECRET, content)
        self.assertIn('"api_url": "https://gw"', content)


class TestOpenCodeContainedReads(_Base):
    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_config_refused(self):
        oc = self.proj / ".opencode"
        oc.mkdir()
        os.symlink(self.outside, oc / "opencode.json")
        projects = {}
        MacOSOpenCodeRulesExtractor()._extract_rules_from_opencode_directory(oc, projects)
        self.assertNotIn(SECRET, self._all_content(build_project_list(projects)))

    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_root_sibling_refused(self):
        oc = self.proj / ".opencode"
        oc.mkdir()
        os.symlink(self.outside, self.proj / "opencode.jsonc")
        projects = {}
        MacOSOpenCodeRulesExtractor()._extract_rules_from_opencode_directory(oc, projects)
        self.assertEqual(build_project_list(projects), [])

    def test_provider_api_key_redacted(self):
        oc = self.proj / ".opencode"
        oc.mkdir()
        (oc / "opencode.json").write_text(
            '{"provider": {"anthropic": {"options": {"apiKey": "%s"}}}, "model": "anthropic/x"}' % SECRET,
            encoding="utf-8",
        )
        projects = {}
        MacOSOpenCodeRulesExtractor()._extract_rules_from_opencode_directory(oc, projects)
        content = self._all_content(build_project_list(projects))
        self.assertNotIn(SECRET, content)
        self.assertIn('"model": "anthropic/x"', content)

    def test_markdown_rules_not_redacted(self):
        # Redaction is scoped to .json/.jsonc; prose mentioning "token" is untouched.
        oc = self.proj / ".opencode"
        (oc / "agents").mkdir(parents=True)
        (oc / "agents" / "a.md").write_text('Set "token": "abc" in the env.', encoding="utf-8")
        projects = {}
        MacOSOpenCodeRulesExtractor()._extract_rules_from_opencode_directory(oc, projects)
        self.assertIn('"token": "abc"', self._all_content(build_project_list(projects)))


if __name__ == "__main__":
    unittest.main()
