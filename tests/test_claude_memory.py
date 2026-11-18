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
import scripts.coding_discovery_tools.memory_secret_rules as memory_secret_rules
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.constants import MAX_CONFIG_FILE_SIZE, MAX_SEARCH_DEPTH
from scripts.coding_discovery_tools.s3_uploader import compute_payload_hash
from tests.test_queue_drain_budget import _DrivesMain, _SimulatedClock

FAKE_SECRET = "ghp_" + "a1B2c3D4" * 4 + "e5F6"
EMPTY_BLOCK_HASH = hashlib.sha256(
    json.dumps({"rules_version": cmh.RULES_VERSION, "folders": []}, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
SLUG = "-work-shop"

# Must equal the same constant in the other repo (coding-discovery-tool / ai-gateway-data).
MEMORY_SECRET_RULES_SHA256 = "65eee9112ee1bc91e69da5ec022a723ef763b7389ac665f68b0d58a9d75b62c8"

PRIVATE_KEY_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n" + "MIIEowIBAAKCAQEA1b2c3d4e5f6g7h8i9j0k1l2m3n4\n" * 3
    + "-----END RSA PRIVATE KEY-----"
)

# (label, line); all values are fake. The same lists are kept in both repos' tests.
SHOULD_MASK = [
    ("AWS access key", "aws key: AKIAIOSFODNN7EXAMPLE"),
    ("GitHub PAT", "token: ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
    ("OpenAI project key", "OPENAI_API_KEY=sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7cDe0fGh3iJk6l"),
    ("Stripe live key", "stripe: sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"),
    ("Slack bot token", "slack: xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
    ("npm token", "//registry.npmjs.org/:_authToken=npm_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
    ("JWT", "auth: eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
     + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("Discord bot token", "discord: MTk4NjIyNDgzNDcxOTI1MjQ4" + ".Cl2FMQ" + ".ZnCjm1XVW7vRze4b7Cq4se7kKWs"),
    ("Private key block", PRIVATE_KEY_BLOCK),
    ("Postgres URL with password", "DATABASE_URL=postgres://app:S3cretPass9@db.internal:5432/app"),
    ("MongoDB SRV URL with password", "mongodb+srv://admin:Adm1nPass99@cluster0.abc12.mongodb.net/prod"),
    ("Redis URL with password", "REDIS_URL=redis://:R3disPass9word@cache.internal:6379/0"),
    ("HTTPS basic auth in URL", "git remote: https://deploy:hunter2pass9@git.example.com/repo.git"),
    ("MySQL URL with password", "mysql://root:R00tPass!@10.0.0.5:3306/app"),
    ("Quoted password", 'password = "Sup3rS3cret!"'),
    ("JSON password", '{"password": "Sup3rS3cret!"}'),
    ("JSON api key", '{"api_key": "a1b2c3d4e5f6g7h8i9j0"}'),
    ("JSON secret", '{"secret": "q8Zr2LmP0xVb7NcT"}'),
    ("Multi-line quoted password", 'password =\n  "Sup3rS3cretValue"'),
    ("YAML unquoted password", "password: Sup3rS3cret9"),
    ("Env var password", "DB_PASSWORD=Sup3rS3cret9"),
    ("Terraform var password", "TF_VAR_db_password=Pa55w0rdProd"),
    ("Env var token", "export API_TOKEN=a1b2c3d4e5f6g7h8"),
    ("Passphrase", "passphrase: c0rrect-h0rse-battery"),
    ("camelCase apiKey", "apiKey: AbC123dEf456GhI789"),
    ("SECRET_KEY_BASE", "SECRET_KEY_BASE=9f8e7d6c5b4a3a2b1c0d"),
    ("Password key ending in said", "db_password_said=Hunter2hunter2"),
    ("AWS secret starting with slash", "aws_secret_access_key=/K7MDENGbPxRfiCYzz12abQ"),
    ("AWS secret with inner slashes", "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCY1"),
    ("API key in URL query", "curl https://api.example.com/v1/data?api_key=a1b2c3d4e5f6g7h8i9"),
    ("Presigned URL signature", "https://b.s3.amazonaws.com/f.zip?X-Amz-Signature=9f86d081884c7d659a2feaa0c55ad015"),
    ("Webhook token in query", "https://api.example.com/hook?token=tk_8Hq2Lz9Xv4Pw"),
    ("Bearer header in curl", "curl -H 'Authorization: Bearer abc123def456ghi789jkl' https://api.example.com"),
    ("Bearer header", "Authorization: Bearer abc123def456ghi789jkl"),
    ("Bearer in capitals", "Authorization: BEARER abc123def456ghi789jkl"),
    ("Quoted password starting with a placeholder word", 'password = "Password123!"'),
    ("Quoted password starting with a dollar", 'password = "$up3rS3cret!"'),
    ("Lowercase joined secret key", "secretkey: abc123def456ghi"),
    ("All-digit password", "DB_PASSWORD=20240101"),
    ("Hex key in URL query", "https://api.example.com/x?key=" + "8A3F09B2C4D5E6F708192A3B4C5D6E7F"),
    ("UUID key in URL query", "https://api.example.com/data?key=" + "6a41c739-57cf-46a9-b53d-cda412bb3d24"),
    ("Mixed-case hyphenated key in URL query", "https://x.example.com/a?key=" + "abc-DEF-123-ghi-456-JKL-789"),
    ("Long all-digit token", "API_TOKEN=" + "12345678901234567890"),
    ("Database URL with a real password on localhost", "postgres://postgres:Xk9mP2qL7wRz@localhost:5432/app"),
    ("URL password with a percent-encoded character", "postgres://app:%23Xk9mP2qL@db.example.com/app"),
    ("URL password starting with a dollar", "postgres://app:$uperS3cret@db.example.com/app"),
    ("URL password that only starts like a template", "postgres://app:${x}Rl9!secret@db.example.com/app"),
    ("Stock URL password on a real host", "postgres://admin:admin@prod-db.corp.example.com/app"),
    ("Stock URL password on a private IP", "mongodb://root:root@10.0.4.12/app"),
    ("Same-as-username URL password on a dotted host", "amqp://guest:guest@mq.internal:5672"),
    ("Stock URL password on a host that only starts with localhost", "postgres://admin:admin@localhost.evil.com/app"),
    ("URL password that only starts like a command substitution", "postgres://app:$(x)Rl9!secret@db.example.com/app"),
    ("URL password in braces with digits", "postgres://app:{Pr0dPass99}@db.corp.example.com/app"),
    ("URL password template with a real fallback value", "postgres://app:${DB_PASS:-S3cretProd9}@db.example.com/app"),
    ("Google API key with a hyphen in URL query",
     "https://maps.googleapis.com/maps/api/geocode/json?key=" + "AIza" + "SyD-3x4mpl3K3y_abcdefghijklmnopqrst"),
    ("Private key with a short last line", PRIVATE_KEY_BLOCK.replace("-----END", "AB==\n-----END")),
    ("Encrypted private key", PRIVATE_KEY_BLOCK.replace("RSA", "ENCRYPTED")),
]

SHOULD_NOT_MASK = [
    ("Env lookup in code", 'password = os.environ["DB_PASSWORD"]'),
    ("Shell variable", "DB_PASSWORD=${DB_PASSWORD}"),
    ("Placeholder in angle brackets", "password: <your-password>"),
    ("CI secret reference", "token: {{ secrets.GH_TOKEN }}"),
    ("Vault command substitution", 'export VAULT_TOKEN="$(vault print token)"'),
    ("Placeholder changeme", 'password = "changeme"'),
    ("Masked stars in JSON", '{"password": "********"}'),
    ("Redacted placeholder", '"password": "<redacted>"'),
    ("Placeholder AWS key of Xs", "aws key: AKIAXXXXXXXXXXXXXXXX"),
    ("Token TTL setting", "TOKEN_TTL_MS=86400000"),
    ("Token expiry seconds", "token_expiry_seconds=3600"),
    ("Password min length", "password_min_length: 12"),
    ("K8s secret name", "secret_name: prod-db-credentials-v2"),
    ("Secret id key", "client_secret_id=Abc123Def456"),
    ("camelCase id key", "tokenId=abc123def456"),
    ("camelCase name key", "apiKeyName: myservice2"),
    ("Password expiry timestamp", "password_expires_at=1731849600"),
    ("Quoted shell variable", 'password = "$DB_PASSWORD"'),
    ("Private key placeholder", "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"),
    ("pwd under the root home", "pwd: /root/repo2"),
    ("Long docs slug key", "https://docs.example.com/a?key=getting-started-with-v2-guide"),
    ("Local database URL with a stock password", "postgres://postgres:postgres@localhost:5432/app"),
    ("Database URL with a placeholder password", "mysql://user:password@db:3306/app"),
    ("Database URL with variables", "postgres://${DB_USER}:${DB_PASS}@db.internal:5432/app"),
    ("URL password template with a stock fallback on a local host",
     "postgres://app:${POSTGRES_PW:-postgres}@db:5432/app"),
    ("URL with the password same as the username on a bare host name", "amqp://guest:guest@mq:5672"),
    ("Local stock URL at the end of a sentence", "Use postgres://postgres:postgres@localhost."),
    ("Local stock URL in bold", "**postgres://postgres:postgres@localhost**"),
    ("URL password that is a shell variable", "postgres://app:$DB_PASS@db/app"),
    ("URL password that is an angle-bracket placeholder", "postgres://app:<password>@db/app"),
    ("URL password that is a format placeholder", "postgres://app:%s@db/app"),
    ("pwd output", "pwd: /Users/dev/repo2"),
    ("pwd home path", "pwd: ~/code/app-v3"),
    ("Password reset URL", "password_reset_url: https://app.example.com/v2/reset"),
    ("OAuth token endpoint", "token_endpoint: https://login.example.com/oauth2/v2.0/token"),
    ("Secret ARN", "secret_arn: arn:aws:secretsmanager:us-west-2:123456789012:secret:prod/db-AbCdEf"),
    ("Django hasher setting", "PASSWORD_HASHERS=django.contrib.auth.hashers.Argon2PasswordHasher"),
    ("Password file path", "PASSWORD_FILE=/run/secrets/db_password"),
    ("Token cache path", "TOKEN_CACHE=/var/cache/app/token2024"),
    ("Token store Windows path", "TOKEN_STORE=C:\\Users\\me\\token2024"),
    ("Password store relative path", "PASSWORD_STORE=../secrets/store2"),
    ("Token scopes", "GITHUB_TOKEN_SCOPES=repo,workflow,read:org"),
    ("Env reference in JS", "apiKey: process.env.API_KEY"),
    ("Docs link with key param", "see https://docs.example.com/search?key=getting-started-v2"),
    ("Docs link page param", "https://docs.example.com/guide?page=2&sort=asc"),
    ("Postgres URL no password", "postgres://localhost:5432/app"),
    ("SSH git remote", "git clone git@github.com:org/repo.git"),
    ("Password policy prose", "Passwords must be at least 12 characters long."),
    ("UI label constant", 'static let forgotPassword = "Forgot Password?"'),
    ("Bearer in prose", "Use a Bearer token in the Authorization header"),
    ("Bearer shell variable", "Authorization: Bearer $TOKEN"),
    ("Bearer angle placeholder", "Authorization: Bearer <token>"),
    ("Bearer template placeholder", "Authorization: Bearer {{ token }}"),
]


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
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
        sentry_patcher = patch.object(utils_mod, "_SENTRY_DSN", "")
        sentry_patcher.start()
        self.addCleanup(sentry_patcher.stop)
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
        self.assertEqual(flagged["findings"], [{"rule_id": "SECRET-002", "line": 3}])
        self.assertIn(FAKE_SECRET, flagged["content"])
        self.assertEqual(
            set(flagged), {"file_path", "file_name", "content", "size", "last_modified", "truncated", "findings"})
        self.assertNotIn("Prefer small diffs", json.dumps(memories))

    def test_a_secret_in_the_index_stays_in_index_with_its_findings(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "db is " + FAKE_SECRET + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["index"]["findings"], [{"rule_id": "SECRET-002", "line": 1}])
        self.assertEqual(folder["flagged"], [])
        self.assertEqual(folder["counts"], {"total": 1, "flagged": 1, "clean": 0, "truncated": 0})
        self.assertIsNone(folder["project_path"])

    def test_a_credential_on_a_line_with_placeholder_words_is_still_flagged(self):
        memory = _memory_dir(self.home)
        _write(memory / "local.md", "test token, latest example: " + FAKE_SECRET + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["flagged"][0]["findings"], [{"rule_id": "SECRET-002", "line": 1}])

    def test_unquoted_assignments_urls_and_query_strings_are_flagged(self):
        memory = _memory_dir(self.home)
        token, password = "abcd1234" + "efgh", "Hunter2" + "pass"
        _write(memory / "new.md", "\n".join([
            "DB_PASSWORD=" + password + "9",
            "auth_token: " + token,
            "redis://:" + password + "@cache:6379/0",
            "https://example.com/dl?x=1&access_token=" + token + "#top",
            "pwd: " + "Sup3r" + "S3cret!", "password = " + "p@ss" + "w0rd!", "secret=" + "hunter2" + "hunter2",
        ]) + "\n")

        findings = self.collect()["folders"][0]["flagged"][0]["findings"]

        self.assertEqual({(finding["rule_id"], finding["line"]) for finding in findings}, {
            ("SECRET-021", 1), ("SECRET-021", 2), ("SECRET-022", 3), ("SECRET-021", 4), ("SECRET-023", 4),
            ("SECRET-021", 5), ("SECRET-021", 6), ("SECRET-021", 7),
        })

    def test_credentials_under_quoted_json_and_yaml_keys_are_flagged(self):
        memory = _memory_dir(self.home)
        _write(memory / "config.md", "\n".join([
            '{"password": "' + "s3cr3t" + 'Passw0rd!"}',
            '{"api_key": "' + "a1b2c3d4e5" + 'f6g7h8i9j0"}',
            "'db_password': " + "Hunter2" + "pass9",
        ]) + "\n")

        findings = self.collect()["folders"][0]["flagged"][0]["findings"]

        self.assertEqual({(finding["rule_id"], finding["line"]) for finding in findings},
                         {("SECRET-016", 1), ("SECRET-016", 2), ("SECRET-021", 3)})

    def test_unquoted_assignment_placeholder_values_are_not_flagged(self):
        memory = _memory_dir(self.home)
        _write(memory / "env.md", "\n".join([
            "password = $DB_PASSWORD_VALUE", "token: <your-token-here>", "secret={{ vault_secret }}",
            "pwd=%PASSWORD_VALUE%", "api_key: **********", "password: None", "token=required",
            "secret: changeme", "passphrase = XXXXXXXXXX", "password=short", "token: some_function_name",
            "secret = load_secret_from_vault", "api_key: SomeFunctionName",
        ]) + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["counts"], {"total": 1, "flagged": 0, "clean": 1, "truncated": 0})

    def test_two_matches_of_one_rule_on_one_line_are_one_finding(self):
        memory = _memory_dir(self.home)
        _write(memory / "db.md", "primary " + FAKE_SECRET + " replica " + FAKE_SECRET + "\n")

        folder = self.collect()["folders"][0]

        self.assertEqual(folder["flagged"][0]["findings"], [{"rule_id": "SECRET-002", "line": 1}])

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

    def test_the_walk_stops_at_the_directory_bound_and_still_finishes(self):
        memory = _memory_dir(self.home)
        for number in range(5):
            _write(memory / f"topic-{number}" / "note.md", "topic\n")

        with patch.object(cmh, "MAX_DIRS_PER_MEMORY_FOLDER", 3):
            self.assertEqual(self.collect()["folders"][0]["counts"]["total"], 2)

    def test_a_memory_directory_listing_is_capped(self):
        memory = _memory_dir(self.home)
        for number in range(6):
            _write(memory / f"topic-{number}.md", "topic\n")

        with patch.object(cmh, "MAX_DIR_ENTRIES_SCANNED", 3):
            self.assertEqual(self.collect()["folders"][0]["counts"]["total"], 3)

    @unittest.skipUnless(os.name == "posix", "needs POSIX symlinks")
    def test_a_self_looping_link_does_not_hide_the_folder(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "index\n")
        os.symlink("loop", memory / "loop")

        self.assertEqual(self.collect()["folders"][0]["counts"]["total"], 1)

    def test_project_folders_are_capped_in_name_order(self):
        for slug in ("-c", "-a", "-b"):
            _write(_memory_dir(self.home, slug) / "MEMORY.md", "index\n")

        with patch.object(cmh, "MAX_PROJECT_FOLDERS", 2):
            paths = [folder["path"] for folder in self.collect()["folders"]]

        self.assertEqual(paths, [str(_memory_dir(self.home, "-a")), str(_memory_dir(self.home, "-b"))])

    def test_subagent_folders_per_base_directory_are_capped_in_name_order(self):
        for name in ("c", "a", "b"):
            _write(self.home / ".claude" / "agent-memory" / name / "MEMORY.md", "index\n")

        with patch.object(cmh, "MAX_SUBAGENT_FOLDERS_PER_BASE", 2):
            names = [folder["agent_name"] for folder in self.collect()["folders"]]

        self.assertEqual(names, ["a", "b"])

    def test_directory_entries_examined_are_capped_and_stop_at_the_deadline(self):
        for name in "abcdef":
            _write(self.home / ".claude" / "agent-memory" / name / "MEMORY.md", "index\n")

        with patch.object(cmh, "MAX_DIR_ENTRIES_SCANNED", 3):
            examined = len(self.collect()["folders"])

        self.assertEqual(examined, 3)
        self.assertEqual(cmh._subdirs(self.home / ".claude" / "agent-memory", 10, deadline=0.0), [])

    def test_transcripts_examined_per_project_folder_are_capped(self):
        project = self.home / ".claude" / "projects" / SLUG
        for name in "abcd":
            _write(project / (name + ".jsonl"), json.dumps({"type": "summary"}) + "\n")

        with patch.object(cmh, "MAX_DIR_ENTRIES_SCANNED", 2):
            _, read_names = self.collect_recording_reads()

        self.assertEqual(len([name for name in read_names if name.endswith(".jsonl")]), 2)

    def test_memory_folders_per_user_are_capped(self):
        for slug in ("-a", "-b", "-c"):
            _write(_memory_dir(self.home, slug) / "MEMORY.md", "index\n")

        with patch.object(cmh, "MAX_MEMORY_FOLDERS", 2):
            paths = [folder["path"] for folder in self.collect()["folders"]]

        self.assertEqual(paths, [str(_memory_dir(self.home, "-a")), str(_memory_dir(self.home, "-b"))])

    def test_flagged_files_beyond_the_per_folder_cap_are_counted_not_sent(self):
        memory = _memory_dir(self.home)
        _write(memory / "MEMORY.md", "index\n")
        for number in range(4):
            _write(memory / f"db{number}.md", "url: " + FAKE_SECRET + "\n")

        with patch.object(cmh, "MAX_FLAGGED_FILES_PER_FOLDER", 2):
            folder = self.collect()["folders"][0]

        self.assertEqual(folder["index"]["content"], "index\n")
        self.assertEqual([item["file_name"] for item in folder["flagged"]], ["db0.md", "db1.md"])
        self.assertEqual(
            folder["counts"], {"total": 5, "flagged": 4, "clean": 1, "truncated": 0, "not_sent": 2})

    def test_the_content_budget_keeps_the_index_first_and_counts_what_does_not_fit(self):
        memory = _memory_dir(self.home)
        flagged_text = "url: " + FAKE_SECRET + "\n"
        _write(memory / "MEMORY.md", "index\n")
        _write(memory / "db0.md", flagged_text)
        _write(memory / "db1.md", flagged_text)

        budget = len(json.dumps("index\n")) + len(json.dumps(flagged_text)) + 5
        with patch.object(cmh, "MAX_MEMORY_CONTENT_BYTES", budget):
            folder = self.collect()["folders"][0]

        self.assertEqual(folder["index"]["content"], "index\n")
        self.assertEqual([item["file_name"] for item in folder["flagged"]], ["db0.md"])
        self.assertEqual(folder["counts"]["not_sent"], 1)

    def test_the_content_budget_is_shared_across_folders(self):
        for slug in ("-a", "-b"):
            _write(_memory_dir(self.home, slug) / "MEMORY.md", "index\n")

        with patch.object(cmh, "MAX_MEMORY_CONTENT_BYTES", len(json.dumps("index\n")) + 1):
            folders = self.collect()["folders"]

        self.assertEqual([bool(folder["index"]) for folder in folders], [True, False])
        self.assertEqual([folder["counts"].get("not_sent", 0) for folder in folders], [0, 1])

    def test_the_content_budget_measures_the_json_escaped_size_that_is_sent(self):
        memory = _memory_dir(self.home)
        escaped_text = "url: " + FAKE_SECRET + "\x01" * 20 + "\n"
        _write(memory / "MEMORY.md", "index\n")
        _write(memory / "db.md", escaped_text)

        with patch.object(cmh, "MAX_MEMORY_CONTENT_BYTES", len(json.dumps("index\n")) + len(escaped_text) + 5):
            folder = self.collect()["folders"][0]

        self.assertEqual(folder["flagged"], [])
        self.assertEqual(folder["counts"]["not_sent"], 1)

    def test_url_hosts_are_capped_per_file_and_per_folder(self):
        memory = _memory_dir(self.home)
        _write(memory / "one.md", "https://a.example.com https://b.example.com https://c.example.com\n")
        _write(memory / "two.md", "https://d.example.com https://e.example.com\n")

        with patch.object(cmh, "MAX_URL_HOSTS_PER_FILE", 2), patch.object(cmh, "MAX_URL_HOSTS_PER_FOLDER", 3):
            hosts = self.collect()["folders"][0]["url_hosts"]

        self.assertEqual(hosts, ["a.example.com", "b.example.com", "d.example.com"])

    def test_findings_are_capped_per_file(self):
        memory = _memory_dir(self.home)
        _write(memory / "db.md", "".join(f"line {number} {FAKE_SECRET}\n" for number in range(3)))

        with patch.object(cmh, "MAX_FINDINGS_PER_FILE", 2):
            folder = self.collect()["folders"][0]

        self.assertEqual([finding["line"] for finding in folder["flagged"][0]["findings"]], [1, 2])

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
        project = self.home / "work" / "shop"
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

        self.assertEqual(
            self.collect(), {"hash": EMPTY_BLOCK_HASH, "rules_version": cmh.RULES_VERSION, "folders": []})

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

    def test_a_rules_version_bump_changes_the_hash_and_the_block_carries_the_version(self):
        _write(_memory_dir(self.home) / "MEMORY.md", "index\n")
        first = self.collect()
        with patch.object(cmh, "RULES_VERSION", cmh.RULES_VERSION + 1):
            bumped = self.collect()

        self.assertEqual(first["rules_version"], cmh.RULES_VERSION)
        self.assertEqual(bumped["rules_version"], cmh.RULES_VERSION + 1)
        self.assertNotEqual(bumped["hash"], first["hash"])

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
        self.assertEqual(entry["findings"], [{"rule_id": "SECRET-002", "line": 1}])
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
        cached = json.loads(self.cache_file.read_text())["users"]["alice"]
        self.assertEqual(set(cached), {str(self.memory.parent), str(self.memory / "MEMORY.md")})

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

    def test_transcripts_are_not_read_once_out_of_time(self):
        _write(self.memory.parent / "session.jsonl", json.dumps({"cwd": "/work/shop"}) + "\n")

        with patch.object(cmh, "MEMORY_SCAN_BUDGET_SECONDS", 0):
            memories, reads = self.collect_recording_reads()

        self.assertIsNone(memories)
        self.assertNotIn("session.jsonl", reads)

    def test_a_project_path_is_resolved_from_transcripts_once(self):
        _write(self.memory.parent / "session.jsonl", json.dumps({"cwd": "/work/shop"}) + "\n")

        cold, cold_reads = self.collect_recording_reads()
        warm, warm_reads = self.collect_recording_reads()

        self.assertIn("session.jsonl", cold_reads)
        self.assertNotIn("session.jsonl", warm_reads)
        self.assertEqual(warm["folders"][0]["project_path"], "/work/shop")
        self.assertEqual(warm, cold)
        self.assertNotIn("session.jsonl", self.cache_file.read_text(encoding="utf-8"))

    def test_a_project_without_a_path_is_read_again_only_after_the_folder_changes(self):
        _write(self.memory.parent / "session.jsonl", json.dumps({"type": "summary"}) + "\n" * cmh.TRANSCRIPT_HEAD_LINES)
        self.collect()

        _, unchanged_reads = self.collect_recording_reads()
        _write(self.memory.parent / "later.jsonl", json.dumps({"cwd": "/work/shop"}) + "\n")
        old_mtime = self.memory.parent.stat().st_mtime_ns
        os.utime(self.memory.parent, ns=(old_mtime + 10**9, old_mtime + 10**9))
        later, changed_reads = self.collect_recording_reads()

        self.assertNotIn("session.jsonl", unchanged_reads)
        self.assertIn("later.jsonl", changed_reads)
        self.assertEqual(later["folders"][0]["project_path"], "/work/shop")

    def test_the_newest_transcripts_are_searched_beyond_their_first_lines(self):
        filler = json.dumps({"type": "summary"}) + "\n" * cmh.TRANSCRIPT_HEAD_LINES
        for name in "abcdef":
            _write(self.memory.parent / (name + ".jsonl"), filler)
        newest = _write(self.memory.parent / "z.jsonl", filler * 2 + json.dumps({"cwd": "/work/shop"}) + "\n")
        os.utime(newest, ns=(newest.stat().st_mtime_ns + 10**9,) * 2)

        self.assertEqual(self.collect()["folders"][0]["project_path"], "/work/shop")

    def test_a_short_transcript_head_without_a_path_is_not_cached_as_missing(self):
        _write(self.memory.parent / "session.jsonl", json.dumps({"type": "summary"}) + "\n")

        self.collect()

        self.assertNotIn(str(self.memory.parent), json.loads(self.cache_file.read_text())["users"]["alice"])

    def test_a_malformed_project_path_entry_is_recomputed(self):
        _write(self.memory.parent / "session.jsonl", json.dumps({"cwd": "/work/shop"}) + "\n")
        self.collect()
        cache = json.loads(self.cache_file.read_text())
        cache["users"]["alice"][str(self.memory.parent)] = {"project_path": 5, "mtime": "x"}
        self.cache_file.write_bytes(json.dumps(cache).encode("utf-8"))

        memories, reads = self.collect_recording_reads()

        self.assertIn("session.jsonl", reads)
        self.assertEqual(memories["folders"][0]["project_path"], "/work/shop")

    def test_a_finished_run_drops_entries_of_removed_files_and_folders(self):
        other = _memory_dir(self.home, "-work-gone")
        _write(other / "MEMORY.md", "index\n")
        _write(other.parent / "session.jsonl", json.dumps({"cwd": "/work/gone"}) + "\n")
        self.collect()
        self.assertIn(str(other.parent), json.loads(self.cache_file.read_text())["users"]["alice"])

        shutil.rmtree(other.parent)
        (self.memory / "clean.md").unlink()
        self.collect()

        self.assertEqual(
            set(json.loads(self.cache_file.read_text())["users"]["alice"]),
            {str(self.memory.parent), str(self.memory / "MEMORY.md"), str(self.memory / "db.md")})

    def test_an_unfinished_run_keeps_at_most_the_entry_cap(self):
        self.collect()

        with patch.object(cmh, "MEMORY_SCAN_BUDGET_SECONDS", 0), patch.object(cmh, "MAX_CACHE_ENTRIES_PER_USER", 2):
            self.assertIsNone(self.collect())

        self.assertEqual(len(json.loads(self.cache_file.read_text())["users"]["alice"]), 2)

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


@unittest.skipIf(
    os.name == "nt", "the main() harness pins platform.system to Linux, which breaks the Windows handle check")
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
        self.assertEqual(folder["flagged"][0]["findings"], [{"rule_id": "SECRET-002", "line": 1}])
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
        self.assertEqual(reports[0]["memories_hash"], EMPTY_BLOCK_HASH)

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


class TestMemorySecretRules(unittest.TestCase):
    """The shared secret rules, line by line; ai-gateway-data keeps the same file and the same lines."""

    def test_the_rule_file_is_the_one_ai_gateway_data_holds(self):
        rules_source = Path(memory_secret_rules.__file__).read_bytes().replace(b"\r\n", b"\n")

        self.assertEqual(hashlib.sha256(rules_source).hexdigest(), MEMORY_SECRET_RULES_SHA256)

    def test_lines_with_a_secret_are_flagged(self):
        for label, line in SHOULD_MASK:
            with self.subTest(label):
                self.assertTrue(memory_secret_rules.find_secrets(line))

    def test_ordinary_lines_are_not_flagged(self):
        for label, line in SHOULD_NOT_MASK:
            with self.subTest(label):
                self.assertEqual(memory_secret_rules.find_secrets(line), [])


if __name__ == "__main__":
    unittest.main()
