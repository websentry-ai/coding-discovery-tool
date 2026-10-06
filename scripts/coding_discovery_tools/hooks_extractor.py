"""
Hooks discovery: the shell commands each coding agent runs on its own events.

Reads every hook config a tool honours (user, project and managed scope) and
returns one entry per hook command, keyed by the project path the backend
groups it under: user and managed hooks go under each user's home (so the
per-user filter keeps them), project hooks under the project root.

When a hook runs a local script, its first 50KB is sent too, so the backend can
rate what the hook actually does, not only its command line. Configs and scripts
are read through the same contained reads as rules (no symlink, hard link or
foreign-owner escapes), and only the program the hook runs is read, never a file
it merely names as an argument.

Unbound's own governance hooks are skipped: they are ours, not a risk to rate.
Only hooks that run Unbound's script from its install location (or the
unbound-hook binary) count as ours; the same file name anywhere else is rated.
"""

import json
import logging
import math
import os
import platform
import re
import shlex
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    from .mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
    from .project_dir_index import dispatch_matches
    from .rule_read_helpers import read_rule_file_contained
except ImportError:  # pragma: no cover - direct-script execution fallback
    from mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
    from project_dir_index import dispatch_matches
    from rule_read_helpers import read_rule_file_contained

logger = logging.getLogger(__name__)

MAX_SCRIPT_SIZE = 50 * 1024  # same cap as rule/skill content (read_rule_file_contained truncates here)
MAX_COMMAND_SIZE = 10240  # the backend's command cap; longer commands are cut (and flagged) before redaction
MAX_HOOK_CONFIG_SIZE = 2 * 1024 * 1024  # settings.json also holds permissions and MCP config, so it can pass 50KB

_MAC_SUPPORT = Path("/Library/Application Support")
_WIN_PROGRAM_FILES = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
_WIN_PROGRAM_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData"))

# tool -> (user-scope files, project-scope files, managed files per OS).
# User and project patterns are relative to the user's home / the project root.
_HOOK_FILES: Dict[str, Tuple[List[str], List[str], Dict[str, List[Path]]]] = {
    "claude code": (
        [".claude/settings.json"],
        [".claude/settings.json", ".claude/settings.local.json"],
        {"Darwin": [_MAC_SUPPORT / "ClaudeCode/managed-settings.json", _MAC_SUPPORT / "ClaudeCode/managed-settings.d/*.json"],
         "Linux": [Path("/etc/claude-code/managed-settings.json"), Path("/etc/claude-code/managed-settings.d/*.json")],
         "Windows": [_WIN_PROGRAM_FILES / "ClaudeCode/managed-settings.json",
                     _WIN_PROGRAM_FILES / "ClaudeCode/managed-settings.d/*.json"]},
    ),
    "cursor": (
        [".cursor/hooks.json"],
        [".cursor/hooks.json"],
        {"Darwin": [_MAC_SUPPORT / "Cursor/hooks.json"], "Linux": [Path("/etc/cursor/hooks.json")],
         "Windows": [_WIN_PROGRAM_DATA / "Cursor/hooks.json"]},
    ),
    "codex": (
        [".codex/hooks.json"],
        [".codex/hooks.json"],
        {"Darwin": [_MAC_SUPPORT / "Codex/hooks.json"], "Linux": [Path("/etc/codex/hooks.json")],
         "Windows": [_WIN_PROGRAM_FILES / "Codex/hooks.json"]},
    ),
    "gemini cli": (
        [".gemini/settings.json"],
        [".gemini/settings.json"],
        {"Darwin": [_MAC_SUPPORT / "GeminiCli/settings.json"], "Linux": [Path("/etc/gemini-cli/settings.json")],
         "Windows": [_WIN_PROGRAM_DATA / "gemini-cli/settings.json"]},
    ),
    "github copilot": ([".copilot/hooks/*.json"], [".github/hooks/*.json"], {}),
    "augment": (
        [".augment/settings.json"],
        [],
        {"Darwin": [Path("/etc/augment/settings.json")], "Linux": [Path("/etc/augment/settings.json")],
         "Windows": [_WIN_PROGRAM_DATA / "Augment/settings.json"]},
    ),
}

# Tools whose user and managed hooks resolve relative paths against the config file's folder.
_CONFIG_RELATIVE_TOOLS = {"cursor", "github copilot"}
# Programs that run a script named in their arguments, by option family; any other program is the hook itself.
_INTERPRETERS = {"bash": "shell", "sh": "shell", "zsh": "shell", "dash": "shell", "fish": "shell",
                 "python": "python", "python3": "python", "node": "node", "deno": "node", "bun": "node",
                 "ruby": "ruby", "perl": "ruby", "pwsh": "powershell", "powershell": "powershell", "cmd": "cmd"}
# Options that take no value, so the next word may be the script; any other option means no file is read.
_FLAG_ONLY_OPTIONS = {
    "shell": {"-e", "-x", "-u", "-l", "-v", "-eu", "-ex", "-xe", "-eux"},
    "python": {"-u", "-B", "-E", "-I", "-O", "-OO", "-s", "-S", "-q", "-b", "-bb", "-v", "-P"},
    "node": {"--no-warnings", "--enable-source-maps", "--trace-warnings"},
    "ruby": {"-w"},
    "powershell": {"-noprofile", "-nop", "-nologo", "-noninteractive"},
    "cmd": set(),
}
_POWERSHELL_VALUE_OPTIONS = {"-executionpolicy", "-ep"}
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Shell operators that chain a second command after the first.
_SHELL_CHAINING = (";", "&", "|", "`", "$(", "\n", ">", "<")
_REDACTED = "***REDACTED***"
# Credentials as they appear in shell commands and scripts, redacted before anything leaves the machine.
# A name that marks a credential; values after it are redacted, code after it (calls, attribute reads) is kept.
_KEY = (r"[A-Za-z0-9_-]{0,64}(?:token|api[-_]?key|apikey|access[-_]?key|secret|password|passwd|pwd|credential"
        r"|private[-_]?key|auth(?![a-z])|dsn|[-_]key(?![a-z])|[-_]pass(?![a-z])|[-_]pwd|pw(?![a-z]))[A-Za-z0-9_-]{0,64}")  # not author or keyboard
_QUOTED = ("(?:[rRbBuUfF]{1,2}|\\$)?(?:'''[\\s\\S]*?(?:'''|\\Z)" + '|"""[\\s\\S]*?(?:"""|\\Z)'  # triple-quoted; a cut-off file ends it
           + r"""|'(?:[^'\\]|\\.)*(?:'|\Z)|"(?:[^"\\]|\\.)*(?:"|\Z)|`(?:[^`\\]|\\.)*(?:`|\Z))""")  # may span lines
# The whole shell word: joined quoted pieces, escapes and bare text ("a"b\ c), unless it starts as code (a call or index).
_VALUE = r"""(?![A-Za-z_][\w.]*[\[(])(?:""" + _QUOTED + r"""|\\.|[^\s'"$(`;|&,)}\][\\]+)+"""
_SECRET_PATTERNS = [
    # Cookie and session headers carry the session itself; redact the whole value.
    re.compile(r"(?i)(\b(?:set-)?cookie['\"]?\s*[:=]\s*['\"]?)[^'\"\n]+"),
    # A bearer or basic credential wherever it appears, e.g. HDR="Bearer abc123".
    re.compile(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"),
    # Headers: Authorization, X-Api-Key, X-Auth-Token and friends.
    re.compile(r"(?i)(\b(?:authorization|proxy-authorization|x-[a-z0-9-]{0,64}(?:key|token|secret|auth|session)[a-z0-9-]{0,64}|[a-z0-9-]{0,64}api-key)"
               r"['\"]?\]?\s*[:=]\s*['\"]?(?:(?:bearer|basic|token)\s+)?)[^\s'\"]+"),
    # The password of a (user, password) pair: auth=("alice", "pw"), HTTPBasicAuth("alice", "pw").
    re.compile(r"""((?:\b\w*Auth\s*\(|(?i:\bauth)\s*=\s*\()\s*(?:""" + _QUOTED + r"""|[\w.]+)\s*,\s*)""" + _QUOTED),
    # Argument arrays: ["--api-key", "VALUE"].
    re.compile(r"""(?i)(['"]--?""" + _KEY + r"""['"]\s*,\s*)(?:'[^']*'|"[^"]*")"""),
    # Flags: --api-key VALUE, --password='a b', -token=x.
    re.compile(r"(?i)(--?" + _KEY + r"(?:=|\s+))" + _VALUE),
    # Assignments in any case and quoting: token=x, password = "a b", "api_key": "x", PASSWORD='a b'.
    re.compile(r"(?i)(\b" + _KEY + r"['\"]?\]?\s*(?::\s*[\w.\[\], |]+?\s*=|[=:])\s*\(?\s*)" + _VALUE),
    # curl -u / --user user:pass
    re.compile(r"(\b(?:curl|wget)\b[^\n|;&]*?(?:\s(?:--user|--proxy-user)(?![\w-])|\s-[uU])(?:=|\s*))" + _VALUE),  # the whole word; not --user-agent
    # URL userinfo and credential query parameters.
    re.compile(r"(://)[^/\s:@'\"]+(?::[^/\s@'\"]+)?(?=@)"),  # user:pass@ and key-only userinfo (Sentry DSNs)
    # mysql -pSECRET, sshpass -p SECRET
    re.compile(r"(\b(?:mysql|mysqldump|mariadb)\b[^\n]*?\s-p)(?!\s)(?:" + _QUOTED + r"""|\\.|[^\s'"`$;|&]+)+"""),
    re.compile(r"(\bsshpass\s+-p\s*)(?:" + _QUOTED + r"""|\\.|[^\s'"`$;|&]+)+"""),
    re.compile(r"(?i)([?&](?:token|key|api_key|apikey|secret|sig|signature|access_token|auth|password)=)[^&\s'\"]+"),
    # Webhook URLs whose secret is the path.
    re.compile(r"(?i)(https://(?:hooks\.slack\.com/(?:services|workflows|triggers)/|(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/"
               r"|[a-z0-9.-]*\.webhook\.office\.com/))[^\s'\"]+"),
    # Azure connection strings and SAS signatures, JWTs, netrc-style "password value".
    re.compile(r"(?i)(\b(?:AccountKey|SharedAccessKey|SharedAccessSignature)=)[^;\s'\"]+"),
    re.compile(r"(?i)(\bsig=)[^&;\s'\"]+"),
    re.compile(r"()\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"),
    re.compile(r"(?i)(\b(?:password|passwd)[ \t]+)(?![=:])[^\s'\"]+"),
    # Private key blocks anywhere.
    re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)[\s\S]*?(?=-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    # Known token formats anywhere.
    re.compile(r"()\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
               r"|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}|A[KS]IA[0-9A-Z]{16}|npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,})"),
]


# Fallback for secrets under any name: a long random-looking token (letters and digits, high entropy).
_RANDOM_TOKEN = re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_-]{32,}={0,2}(?![\w/.-])")


def _looks_random(token: str) -> bool:
    if not (re.search(r"[A-Za-z]", token) and re.search(r"\d", token)):
        return False
    counts = {c: token.count(c) for c in set(token)}
    entropy = -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())
    return entropy >= 4.0


def redact_secrets(text: str) -> str:
    """Hook command or script with inline credentials replaced; the code around them is kept."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + _REDACTED, text)
    return _RANDOM_TOKEN.sub(lambda m: _REDACTED if _looks_random(m.group(0)) else m.group(0), text)
_UNBOUND_HOOK_BINARY = Path("/opt/unbound/current/unbound-hook/unbound-hook")


def _spec_key(tool_name: str) -> Optional[str]:
    """The _HOOK_FILES key for a tool row, or None when that product reads no hook files."""
    name = tool_name.lower()
    if name.startswith("augment (") or name == "auggie cli":
        return "augment"
    # Copilot's hooks dir is read by the CLI and by Copilot in VS Code; other Copilot surfaces don't run it.
    # Copilot's ~/.copilot hooks: the merge step attaches them to the CLI and the one canonical editor row only.
    if name.startswith("github copilot"):
        return "github copilot"
    if name == "cursor cli":
        return "cursor"  # cursor-agent reads the same .cursor/hooks.json as the IDE
    return name if name in _HOOK_FILES else None


def hook_files_for(tool_name: str) -> Optional[Tuple[List[str], List[str], List[Path]]]:
    """(user, project, managed) hook file patterns for this tool on this OS, or None."""
    key = _spec_key(tool_name)
    if key is None:
        return None
    user, project, managed = _HOOK_FILES[key]
    return user, project, managed.get(platform.system(), [])


def _expand(path: Path) -> List[Path]:
    """The files a (possibly `*.json`) pattern names; existence only, contents are read contained."""
    if "*" in path.name:
        return sorted(path.parent.glob(path.name)) if path.parent.is_dir() else []
    return [path] if path.is_file() else []


def _load_hooks(path: Path, root: Path, follow_symlinks: bool) -> Dict:
    read = read_rule_file_contained(path, root, allow_symlink=follow_symlinks, max_size=MAX_HOOK_CONFIG_SIZE)
    if read and read[1]:
        logger.warning(f"  hooks: {path} is over {MAX_HOOK_CONFIG_SIZE} bytes, skipped")
        return {}
    text = read[0] if read else None
    try:
        data = json.loads(text) if text else None
    except ValueError:
        try:
            data = json.loads(_strip_trailing_commas(_strip_jsonc_comments(text)))  # Gemini and Augment settings are JSONC
        except ValueError as e:
            logger.debug(f"  hooks: could not parse {path}: {e}")
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
    # Copilot entries can carry both shells; report the one this platform runs.
    shells = ("powershell", "bash") if platform.system() == "Windows" else ("bash", "powershell")
    for key in ("command", *shells, "prompt"):
        value = hook.get(key)
        if isinstance(value, str) and value.strip():
            return hook_type, value
    return hook_type, ""


def _tokens(command: str) -> List[str]:
    """Shell words, with ; & | < > ( ) as their own tokens outside quotes; Windows paths keep their backslashes."""
    posix = platform.system() != "Windows"
    try:
        lexer = shlex.shlex(command, posix=posix, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = command.split()
    return words if posix else [w.strip('"\'') for w in words]


def _script_argument(args: List[str], family: str) -> Optional[int]:
    """Index of the script an interpreter runs, or None when an option could be inline code, a module or a value."""
    i = 0
    while i < len(args):
        word = args[i]
        if family == "cmd" and word.lower() in ("/c", "/k", "/q", "/d", "/s"):
            i += 1  # cmd /c script.bat runs script.bat
            continue
        if family == "node" and word == "run":
            i += 1  # deno run / bun run script.ts
            continue
        if not word.startswith("-"):
            if family == "node" and "/" not in word and "\\" not in word and "." not in word:
                return None  # bun test, bun x pkg: a subcommand or package, not a file
            return i
        option = word.lower() if family == "powershell" else word
        if family == "powershell" and option in ("-file", "-f"):
            return i + 1 if i + 1 < len(args) else None
        if family == "powershell" and option in _POWERSHELL_VALUE_OPTIONS:
            i += 2
            continue
        deno_permission = family == "node" and (option == "-A" or option.startswith(("--allow-", "--deny-")))
        if option not in _FLAG_ONLY_OPTIONS[family] and not deno_permission:
            return None
        i += 1
    return None


def _quote_of(command: str, index: int, count: int) -> str:
    """The quote that opens shell word ``index``: "'", '"' or ''. Unknown: '"' without single quotes, else "'"."""
    if "\\$" in command or "\\~" in command:
        return "'"  # an escaped \$VAR or \~ is literal; shlex has already dropped the backslash
    unknown = "'" if "'" in command else '"'  # with no single quotes anywhere, $VAR expands; ~ may be quoted
    try:
        raw = shlex.split(command, posix=False)
    except ValueError:
        return unknown
    if len(raw) != count:
        return unknown
    return raw[index][0] if raw[index][:1] in ("'", '"') else ""


def _program_path(command: str, config_dir: Optional[Path], project_root: Optional[Path], home: Path) -> Optional[Path]:
    """The file the hook runs: its first word, or the script an interpreter is given. Never an argument."""
    words = _tokens(command)
    if not words:
        return None
    index = 0
    if Path(words[0]).name.lower() == "env":
        index = 1  # `/usr/bin/env [-i] [NAME=value] cmd` runs cmd
        while index < len(words) - 1 and words[index] in ("-i", "--ignore-environment", "-"):
            index += 1
        if index >= len(words) or words[index].startswith("-"):
            return None  # bare env, or env options that take values (-u NAME, -S ...); don't guess
    while index < len(words) - 1 and _ENV_ASSIGNMENT.match(words[index]):
        index += 1  # `NAME=value cmd` runs cmd
    family = _INTERPRETERS.get(re.sub(r"(?<=[a-z])[\d.]+$", "", Path(words[index]).name.lower().removesuffix(".exe")))
    if family is not None:
        script = _script_argument(words[index + 1:], family)
        if script is None:
            return None
        index += script + 1
    quote = _quote_of(command, index, len(words))
    program = words[index]
    if project_root is None and "CLAUDE_PROJECT_DIR" in program:
        return None  # a user hook's project is whichever one is open when it runs; no single file to read
    # The shell expands ~ only unquoted and $VAR only outside single quotes; a quoted literal names no real file.
    if not quote and (program.startswith("~/") or program.startswith("~\\")):
        program = str(home) + program[1:]
    if quote != "'":
        for var, value in (("HOME", str(home)), ("CLAUDE_PROJECT_DIR", str(project_root or home))):
            program = program.replace(f"${{{var}}}", value).replace(f"${var}", value)
    path = Path(program)
    if family in (None, "cmd") and "/" not in program and "\\" not in program:
        return None  # a bare command name is found through PATH, never a local file of that name
    if path.is_absolute():
        return path
    # Project hooks run from the project root; Cursor and Copilot user hooks from their config dir. Others: unknown.
    base = project_root if project_root is not None else config_dir
    return base / path if base is not None else None


def _is_unbound_hook(command: str, program: Optional[Path], unbound_scripts: Set[Path]) -> bool:
    """Unbound's hook as its installers write it: the unbound-hook binary, or its script at an install location."""
    if any(op in command for op in _SHELL_CHAINING):
        return False  # a second command after Unbound's must stay visible
    words = _tokens(command)
    if len(words) >= 2 and Path(words[0]) == _UNBOUND_HOOK_BINARY and words[1] == "hook":
        return True
    return program is not None and Path(os.path.normpath(str(program))) in unbound_scripts


def _unbound_scripts(home: Path, project_root: Optional[Path], managed_dirs: List[Path]) -> Set[Path]:
    """Unbound's hook script in the root-owned managed config dirs. A copy under a user's home is user-writable,
    so it is reported and the backend decides from its content whether it is Unbound's."""
    return {Path(os.path.normpath(str(d / "hooks" / "unbound.py"))) for d in managed_dirs}


def hooks_from_settings(hooks: Dict, source: str, tool_name: str = "Claude Code") -> List[Dict]:
    """Managed hooks from a settings object another extractor already loaded (the Claude MDM plist)."""
    files = hook_files_for(tool_name)
    managed_dirs = sorted({p.parent for p in files[2]}) if files else []
    home = Path.home()
    unbound = _unbound_scripts(home, None, managed_dirs)
    found = []
    for event, matcher, hook in _iter_commands(hooks if isinstance(hooks, dict) else {}):
        hook_type, command = _command_of(hook)
        program = _program_path(command, None, None, home) if command else None
        if command and not _is_unbound_hook(command, program, unbound):
            item = {"event": event, "matcher": matcher, "type": hook_type,
                    "command": redact_secrets(command[:MAX_COMMAND_SIZE]), "file_path": source, "scope": "managed"}
            if len(command) > MAX_COMMAND_SIZE:
                item["truncated"] = True
            found.append(item)
    return found


# Files a hook can run that are scripts; anything else (a dotfile, a config) is never attached.
_SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".fish", ".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".rb", ".pl",
                    ".ps1", ".psm1", ".bat", ".cmd", ".php", ".lua"}
_CREDENTIAL_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".gcloud"}
_CREDENTIAL_FILES = {".netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc"}


def _looks_like_script(path: Path, text: str) -> bool:
    """A script by extension, shebang or executable bit, and never a file from a credential folder."""
    parts = {part.lower() for part in path.parts}
    if parts & _CREDENTIAL_DIRS or path.name.lower() in _CREDENTIAL_FILES or "gh" in parts and ".config" in parts:
        return False
    executable = platform.system() != "Windows" and os.access(path, os.X_OK)  # Windows reports X_OK for any file
    return path.suffix.lower() in _SCRIPT_SUFFIXES or text.startswith("#!") or executable


def _hooks_in_file(path: Path, scope: str, home: Path, project_root: Optional[Path], root: Path,
                   follow_symlinks: bool, unbound_scripts: Set[Path], config_relative: bool = True) -> List[Dict]:
    """Hooks in one config, read contained under ``root``; a script outside ``root`` is not read."""
    found = []
    for event, matcher, hook in _iter_commands(_load_hooks(path, root, follow_symlinks)):
        hook_type, command = _command_of(hook)
        if not command:
            continue
        config_dir = path.parent if config_relative else None
        program = _program_path(command, config_dir, project_root, home) if hook_type == "command" else None
        if _is_unbound_hook(command, program, unbound_scripts):
            continue
        item = {"event": event, "matcher": matcher, "type": hook_type,
                "command": redact_secrets(command[:MAX_COMMAND_SIZE]), "file_path": str(path), "scope": scope}
        try:
            read = (read_rule_file_contained(program, root, allow_symlink=follow_symlinks)
                    if program is not None and program.is_file() else None)
        except OSError:
            read = None  # an unreadable script costs only its own content, not the file's other hooks
        if read and read[0] and "\x00" not in read[0] and _looks_like_script(program, read[0]):
            item["script_path"] = str(program)
            item["script_content"] = redact_secrets(read[0])
        if len(command) > MAX_COMMAND_SIZE or (read and read[1]):
            item["truncated"] = True  # the backend floors what it cannot see in full
        found.append(item)
    return found


def _project_roots_with_hook_dirs(home: Path, dir_names: Set[str]) -> Set[str]:
    """Repos under ``home`` holding a tool's hook dir, via the scan's shared directory index."""
    system = platform.system()
    try:
        if system == "Darwin":
            from .macos_extraction_helpers import _MACOS_PROJECT_SKIP_ID as skip_id, _macos_project_skip as skip
        elif system == "Linux":
            from .linux_extraction_helpers import _LINUX_PROJECT_SKIP_ID as skip_id, _linux_project_skip as skip
        elif system == "Windows":
            from .windows_extraction_helpers import _WINDOWS_PROJECT_SKIP_ID as skip_id, _windows_project_skip as skip
        else:
            return set()
    except ImportError:  # pragma: no cover - direct-script execution
        return set()
    roots: Set[str] = set()
    dispatch_matches(home, home, skip, skip_id, lambda name: name in dir_names,
                     lambda found: roots.add(str(found.parent)))
    roots.discard(str(home))
    return roots


def _user_config_path(home: Path, pattern: str) -> Tuple[Path, Path]:
    """(path, containment root) of a user-scope pattern; Copilot's ~/.copilot moves to COPILOT_HOME for the running user."""
    override = (os.environ.get("COPILOT_HOME") or "").strip()
    if pattern.startswith(".copilot/") and override and home == Path.home():
        config_dir = Path(os.path.expanduser(os.path.expandvars(override)))
        return config_dir / pattern[len(".copilot/"):], config_dir
    return home / pattern, home


def extract_hooks(tool_name: str, user_homes: List[Path], project_paths: Iterable[str]) -> Dict[str, List[Dict]]:
    """{project path: [hook, ...]} for every hook config this tool reads. Never raises."""
    files = hook_files_for(tool_name)
    if files is None:
        return {}
    user_files, project_files, managed_patterns = files
    config_relative = _spec_key(tool_name) in _CONFIG_RELATIVE_TOOLS
    by_project: Dict[str, List[Dict]] = {}
    managed_dirs = sorted({p.parent for p in managed_patterns})
    project_dir_names = {pattern.split("/")[0] for pattern in project_files}
    projects = set(project_paths)
    managed_files = []
    for pattern in managed_patterns:
        try:
            managed_files.extend(_expand(pattern))
        except OSError as e:
            logger.debug(f"  hooks: skipped managed {pattern}: {e}")
    # One unreadable home or project never hides the hooks of the others.
    for home in user_homes:
        unbound = _unbound_scripts(home, None, managed_dirs)
        for pattern in user_files:
            try:
                pattern_path, root = _user_config_path(home, pattern)
                for path in _expand(pattern_path):
                    by_project.setdefault(str(home), []).extend(
                        _hooks_in_file(path, "user", home, None, root, True, unbound, config_relative))
            except Exception as e:
                logger.warning(f"  hooks: skipped {home}/{pattern} for {tool_name}: {e}")
        for path in managed_files:
            try:
                by_project.setdefault(str(home), []).extend(
                    _hooks_in_file(path, "managed", home, None, path.parent, False, unbound, config_relative))
            except Exception as e:
                logger.warning(f"  hooks: skipped {path} for {tool_name}: {e}")
        if project_dir_names:
            try:
                projects |= _project_roots_with_hook_dirs(home, project_dir_names)
            except Exception as e:
                logger.warning(f"  hooks: skipped project discovery under {home} for {tool_name}: {e}")
    for project in projects:
        try:
            root = Path(project)
            home = next((h for h in user_homes if root == h or h in root.parents), None)
            if home is None or root == home:
                continue  # outside every scanned home, or the home itself (its user-scope file is read above)
            unbound = _unbound_scripts(home, root, managed_dirs)
            for pattern in project_files:
                for path in _expand(root / pattern):
                    scope = "local" if path.name == "settings.local.json" else "project"
                    by_project.setdefault(project, []).extend(
                        _hooks_in_file(path, scope, home, root, root, False, unbound))
        except Exception as e:
            logger.warning(f"  hooks: skipped {project} for {tool_name}: {e}")
    return {path: hooks for path, hooks in by_project.items() if hooks}
