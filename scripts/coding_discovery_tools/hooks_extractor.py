"""
Hooks discovery: the shell commands each coding agent runs on its own events.

Reads every hook config a tool honours (user, project and managed scope) and
returns one entry per hook command, keyed by the project path the backend
groups it under: user and managed hooks go under each user's home (so the
per-user filter keeps them), project hooks under the project root.

When a hook runs a local script, its first 50KB is sent too, so the
backend can rate what the hook actually does, not only its command line.

Unbound's own governance hooks are skipped: they are ours, not a risk to rate.
"""

import json
import logging
import platform
import shlex
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_SCRIPT_SIZE = 50 * 1024  # same cap as rule/skill content

_MAC_SUPPORT = Path("/Library/Application Support")

# tool name (lowercased) -> (user-scope files, project-scope files, managed files).
# Paths in the first two are relative to the user's home / the project root.
_HOOK_FILES: Dict[str, Tuple[List[str], List[str], Dict[str, List[str]]]] = {
    "claude code": (
        [".claude/settings.json"],
        [".claude/settings.json", ".claude/settings.local.json"],
        {"Darwin": [str(_MAC_SUPPORT / "ClaudeCode/managed-settings.json"),
                    str(_MAC_SUPPORT / "ClaudeCode/managed-settings.d/*.json")],
         "Linux": ["/etc/claude-code/managed-settings.json", "/etc/claude-code/managed-settings.d/*.json"]},
    ),
    "cursor": (
        [".cursor/hooks.json"],
        [".cursor/hooks.json"],
        {"Darwin": [str(_MAC_SUPPORT / "Cursor/hooks.json")], "Linux": ["/etc/cursor/hooks.json"]},
    ),
    "codex": (
        [".codex/hooks.json"],
        [".codex/hooks.json"],
        {"Darwin": [str(_MAC_SUPPORT / "Codex/hooks.json")], "Linux": ["/etc/codex/hooks.json"]},
    ),
    "gemini cli": (
        [".gemini/settings.json"],
        [".gemini/settings.json"],
        {"Darwin": [str(_MAC_SUPPORT / "GeminiCli/settings.json")], "Linux": ["/etc/gemini-cli/settings.json"]},
    ),
    "github copilot cli": ([".copilot/hooks/*.json"], [".github/hooks/*.json"], {}),
    "auggie cli": (
        [".augment/settings.json"],
        [],
        {"Darwin": ["/etc/augment/settings.json"], "Linux": ["/etc/augment/settings.json"]},
    ),
}


def hook_files_for(tool_name: str) -> Optional[Tuple[List[str], List[str], List[str]]]:
    """(user, project, managed) hook file patterns for this tool on this OS, or None."""
    name = tool_name.lower()
    if name.startswith("augment ("):
        name = "auggie cli"
    if name.startswith("github copilot"):
        name = "github copilot cli"
    spec = _HOOK_FILES.get(name)
    if spec is None:
        return None
    user, project, managed = spec
    return user, project, managed.get(platform.system(), [])


def _expand(base: Path, pattern: str) -> List[Path]:
    path = base / pattern
    if "*" in path.name:
        return sorted(path.parent.glob(path.name)) if path.parent.is_dir() else []
    return [path] if path.is_file() else []


def _load_hooks(path: Path) -> Dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug(f"  hooks: could not read {path}: {e}")
        return {}
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return hooks if isinstance(hooks, dict) else {}


def _iter_commands(hooks: Dict) -> Iterable[Tuple[str, str, Dict]]:
    """(event, matcher, hook) for both the nested Claude/Codex/Gemini shape and the flat Cursor/Copilot shape."""
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            matcher = entry.get("matcher") if isinstance(entry.get("matcher"), str) else ""
            nested = entry.get("hooks")
            for hook in (nested if isinstance(nested, list) else [entry]):
                if isinstance(hook, dict):
                    yield event, matcher, hook


def _command_of(hook: Dict) -> Tuple[str, str]:
    """(type, command) of one hook; prompt-type hooks carry their prompt as the command."""
    hook_type = hook.get("type") if isinstance(hook.get("type"), str) else "command"
    for key in ("command", "bash", "powershell", "prompt"):
        value = hook.get(key)
        if isinstance(value, str) and value.strip():
            return hook_type, value
    return hook_type, ""


def _script_of(command: str, config_dir: Path, project_root: Optional[Path], home: Path) -> Tuple[str, str]:
    """(path, body) of the local script the command runs, when one exists and is readable text."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "", ""
    env = {"HOME": str(home), "CLAUDE_PROJECT_DIR": str(project_root or config_dir.parent)}
    for token in tokens[:3]:
        expanded = str(home) + token[1:] if token.startswith("~/") else token
        for var, value in env.items():
            expanded = expanded.replace(f"${var}", value).replace(f"${{{var}}}", value)
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = config_dir / candidate
        try:
            if not candidate.is_file():
                continue
            with open(candidate, "rb") as f:
                head = f.read(MAX_SCRIPT_SIZE)  # longer scripts are truncated, like rule/skill content
            if b"\x00" in head:
                continue  # a compiled binary, not a script
            return str(candidate), head.decode("utf-8", errors="ignore")
        except OSError:
            continue
    return "", ""


def _is_unbound_hook(command: str, script_path: str) -> bool:
    """Unbound's installed hook: a hooks/unbound.py|.sh script (as the installers match it) or the unbound-hook binary."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if len(tokens) >= 2 and Path(tokens[0]).name == "unbound-hook" and tokens[1] == "hook":
        return True
    for candidate in [script_path] + tokens[:3]:
        path = Path(candidate) if candidate else None
        if path and path.name in ("unbound.py", "unbound.sh") and path.parent.name == "hooks":
            return True
    return False


def _hooks_in_file(path: Path, scope: str, home: Path, project_root: Optional[Path]) -> List[Dict]:
    found = []
    for event, matcher, hook in _iter_commands(_load_hooks(path)):
        hook_type, command = _command_of(hook)
        if not command:
            continue
        item = {"event": event, "matcher": matcher, "type": hook_type, "command": command,
                "file_path": str(path), "scope": scope}
        script_path, script = _script_of(command, path.parent, project_root, home)
        if _is_unbound_hook(command, script_path):
            continue
        if script:
            item["script_path"] = script_path
            item["script_content"] = script
        found.append(item)
    return found


def extract_hooks(tool_name: str, user_homes: List[Path], project_paths: Iterable[str]) -> Dict[str, List[Dict]]:
    """{project path: [hook, ...]} for every hook config this tool reads. Never raises."""
    files = hook_files_for(tool_name)
    if files is None:
        return {}
    user_files, project_files, managed_files = files
    by_project: Dict[str, List[Dict]] = {}
    try:
        managed = []
        for pattern in managed_files:
            for path in _expand(Path("/"), pattern.lstrip("/")):
                managed.append(path)
        for home in user_homes:
            for pattern in user_files:
                for path in _expand(home, pattern):
                    by_project.setdefault(str(home), []).extend(_hooks_in_file(path, "user", home, None))
            for path in managed:
                by_project.setdefault(str(home), []).extend(_hooks_in_file(path, "managed", home, None))
        for project in set(project_paths):
            root = Path(project)
            home = next((h for h in user_homes if root == h or h in root.parents), Path.home())
            if root == home:
                continue  # a home dir's .claude/settings.json is the user-scope file read above
            for pattern in project_files:
                for path in _expand(root, pattern):
                    scope = "local" if path.name == "settings.local.json" else "project"
                    by_project.setdefault(project, []).extend(_hooks_in_file(path, scope, home, root))
    except Exception as e:
        logger.warning(f"  hooks: extraction failed for {tool_name}: {e}")
    return {path: hooks for path, hooks in by_project.items() if hooks}
