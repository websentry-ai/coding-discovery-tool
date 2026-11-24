"""
Hook policy: whether a higher-priority admin policy can stop Unbound's hook.

Reported per tool as ``hook_policy`` with exactly five keys, each a value or
None when it can't be told from this machine's files (WEB-6392):

  overriding_policy               "console" | "mdm" | None: the highest-priority
                                  admin source that is not our managed file
  overriding_policy_has_our_hook  whether that source runs Unbound's hook
  merge_sources_enabled           whether lower sources (our file too) still merge in
  hooks_disabled                  whether the tool runs hooks at all
  telemetry_enabled               whether the tool's own telemetry is on

Claude Code ranks its managed sources console (server-managed, cached at
~/.claude/remote-settings.json), then MDM (the com.anthropic.claudecode
profile, or HKLM on Windows), then the managed-settings files Unbound writes.
By default the first source with a policy key wins and the rest are ignored;
``managedSourcesBehavior: "merge"`` makes them combine. The console policy is
cached only once a user approves it, so a missing cache is not proof there is
none. Codex and VS Code report only whether hooks are switched off.

Every read is best-effort: an unreadable or unparseable file makes the answers
that depend on it None and never raises.
"""

import base64
import json
import logging
import os
import platform
import plistlib
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .hooks_extractor import _command_of, _iter_commands, _program_path
    from .mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
    from .rule_read_helpers import read_rule_file_contained
except ImportError:  # pragma: no cover - direct-script execution fallback
    from hooks_extractor import _command_of, _iter_commands, _program_path
    from mcp_extraction_helpers import _strip_jsonc_comments, _strip_trailing_commas
    from rule_read_helpers import read_rule_file_contained

logger = logging.getLogger(__name__)

_WIN_PROGRAM_FILES = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
_WIN_PROGRAM_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
# Where Claude Code reads managed-settings.json (and where setup puts hooks/unbound.py).
CLAUDE_MANAGED_DIRS = {
    "Darwin": Path("/Library/Application Support/ClaudeCode"),
    "Linux": Path("/etc/claude-code"),
    "Windows": _WIN_PROGRAM_FILES / "ClaudeCode",
}
CODEX_SYSTEM_DIRS = {
    "Darwin": Path("/etc/codex"),
    "Linux": Path("/etc/codex"),
    "Windows": _WIN_PROGRAM_DATA / "OpenAI" / "Codex",
}
MANAGED_PREFERENCES_DIR = Path("/Library/Managed Preferences")
UNBOUND_ROOT = Path("/opt/unbound")
_VSCODE_USER_SETTINGS = {
    "Darwin": ("Library", "Application Support", "Code", "User", "settings.json"),
    "Linux": (".config", "Code", "User", "settings.json"),
    "Windows": ("AppData", "Roaming", "Code", "User", "settings.json"),
}
_MAX_POLICY_SIZE = 2 * 1024 * 1024
# Keys that steer how sources combine; a source holding only these is no policy.
_CONTROL_KEYS = {"managedSourcesBehavior", "wslInheritsWindowsSettings"}
_UNREADABLE = object()


def hook_policy_for(tool_name: str, home: Path) -> Optional[Dict]:
    """The hook_policy for one tool as seen by the user at ``home``, or None
    when nothing about it can be told. Never raises."""
    name = (tool_name or "").lower()
    try:
        if name == "claude code":
            policy = _claude_code_policy(home)
        elif name == "codex":
            policy = _policy(hooks_disabled=_codex_hooks_disabled(home))
        elif name.startswith("github copilot") and name.endswith("(vs code)"):
            policy = _policy(hooks_disabled=_vscode_hooks_disabled(home))
        else:
            return None
    except Exception as e:
        logger.debug(f"  hook policy for {tool_name} could not be read: {e}")
        return None
    return policy if any(value is not None for value in policy.values()) else None


def _policy(overriding_policy=None, overriding_policy_has_our_hook=None, merge_sources_enabled=None,
            hooks_disabled=None, telemetry_enabled=None) -> Dict:
    return {"overriding_policy": overriding_policy,
            "overriding_policy_has_our_hook": overriding_policy_has_our_hook,
            "merge_sources_enabled": merge_sources_enabled,
            "hooks_disabled": hooks_disabled,
            "telemetry_enabled": telemetry_enabled}


# -- Claude Code ----------------------------------------------------------------

def _claude_code_policy(home: Path) -> Dict:
    managed_dir = CLAUDE_MANAGED_DIRS.get(platform.system())
    ranked = [("console", _read_json(home / ".claude" / "remote-settings.json", home)),
              ("mdm", _claude_mdm_settings(home)),
              ("file", _claude_managed_files(managed_dir))]
    if any(doc is _UNREADABLE for _, doc in ranked):
        return _policy()  # a source we can't read may be the one in force
    sources = [(label, doc) for label, doc in ranked if isinstance(doc, dict)]
    selected = next(((label, doc) for label, doc in sources if _has_policy_key(doc)), None)
    overriding = selected if selected and selected[0] != "file" else None
    merge = _merge_enabled(sources)
    return _policy(
        overriding_policy=overriding[0] if overriding else None,
        overriding_policy_has_our_hook=(_runs_our_hook(overriding[1], home, managed_dir)
                                        if overriding else None),
        merge_sources_enabled=merge,
        hooks_disabled=_claude_hooks_disabled(sources, selected, merge, home),
        telemetry_enabled=_claude_telemetry(sources, home),
    )


def _has_policy_key(doc: Dict) -> bool:
    return any(key not in _CONTROL_KEYS and value is not None for key, value in doc.items())


def _merge_enabled(sources) -> Optional[bool]:
    """managedSourcesBehavior from the highest source carrying it or a policy key."""
    for _, doc in sources:
        if "managedSourcesBehavior" in doc or _has_policy_key(doc):
            behavior = doc.get("managedSourcesBehavior", "first-wins")
            return {"merge": True, "first-wins": False}.get(behavior)
    return None


def _claude_hooks_disabled(sources, selected, merge: Optional[bool], home: Path) -> Optional[bool]:
    """Only a managed disableAllHooks stops managed hooks; with no managed
    source the user's own settings decide. A non-boolean value is dropped."""
    if selected is None:
        value = _read_json(home / ".claude" / "settings.json", home, follow_symlinks=True)
        return value.get("disableAllHooks") if isinstance(value, dict) and \
            isinstance(value.get("disableAllHooks"), bool) else None
    if merge:
        return next((doc["disableAllHooks"] for _, doc in sources
                     if isinstance(doc.get("disableAllHooks"), bool)), False)
    value = selected[1].get("disableAllHooks")
    return value if isinstance(value, bool) else False


def _claude_telemetry(sources, home: Path) -> Optional[bool]:
    """CLAUDE_CODE_ENABLE_TELEMETRY: managed env merges per variable, highest
    source first, then the user's settings. Unset anywhere: the shell may still
    set it, so None."""
    user = _read_json(home / ".claude" / "settings.json", home, follow_symlinks=True)
    for doc in [doc for _, doc in sources] + ([user] if isinstance(user, dict) else []):
        env = doc.get("env")
        if isinstance(env, dict) and "CLAUDE_CODE_ENABLE_TELEMETRY" in env:
            return str(env["CLAUDE_CODE_ENABLE_TELEMETRY"]).strip().lower() in ("1", "true", "yes", "on")
    return None


def _claude_mdm_settings(home: Path):
    """The MDM policy: the com.anthropic.claudecode profile on macOS (the
    user-level profile's keys over the computer-level one's), HKLM on Windows."""
    system = platform.system()
    if system == "Darwin":
        merged, found = {}, False
        for path in (MANAGED_PREFERENCES_DIR / "com.anthropic.claudecode.plist",
                     MANAGED_PREFERENCES_DIR / home.name / "com.anthropic.claudecode.plist"):
            doc = _read_plist(path)
            if doc is _UNREADABLE:
                return _UNREADABLE
            if isinstance(doc, dict):
                merged.update(doc)
                found = True
        return merged if found else None
    if system == "Windows":
        try:
            text = _read_hklm_claude_settings()
        except OSError:
            return _UNREADABLE
        return _parse_json(text) if text is not None else None
    return None


def _read_hklm_claude_settings() -> Optional[str]:
    """The Settings value under HKLM\\SOFTWARE\\Policies\\ClaudeCode, or None when absent."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\ClaudeCode") as key:
            value, _ = winreg.QueryValueEx(key, "Settings")
    except FileNotFoundError:
        return None
    return value if isinstance(value, str) else None


def _claude_managed_files(managed_dir: Optional[Path]):
    """managed-settings.json then managed-settings.d/*.json in name order,
    combined the way Claude Code does (see _merge_settings)."""
    if managed_dir is None:
        return None
    paths = [managed_dir / "managed-settings.json"]
    dropins = managed_dir / "managed-settings.d"
    if dropins.is_dir():
        paths += sorted(p for p in dropins.glob("*.json") if not p.name.startswith("."))
    merged, found = {}, False
    for path in paths:
        doc = _read_json(path, path.parent)
        if doc is _UNREADABLE:
            return _UNREADABLE
        if isinstance(doc, dict):
            merged = _merge_settings(merged, doc)
            found = True
    return merged if found else None


def _merge_settings(base: Dict, later: Dict) -> Dict:
    """Claude Code's drop-in rules: nested blocks (env, hooks, ...) merge key by
    key, lists combine without duplicates, any other value is replaced by the
    later file's. The few keys it takes whole (fallbackModel, modelPicker, ...)
    are not read here."""
    merged = dict(base)
    for key, value in later.items():
        old = merged.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            merged[key] = _merge_settings(old, value)
        elif isinstance(old, list) and isinstance(value, list):
            merged[key] = old + [item for item in value if item not in old]
        else:
            merged[key] = value
    return merged


def _runs_our_hook(doc: Dict, home: Path, managed_dir: Optional[Path]) -> bool:
    """Whether the policy runs an Unbound hook installed on this machine. One it
    names but this machine lacks (e.g. the script where the binary is installed)
    runs nothing, so it doesn't count. The binary is matched by location (see
    _is_unbound_binary); the scripts by path."""
    targets = [home / ".claude" / "hooks" / "unbound.py"]
    if managed_dir is not None:
        targets.append(managed_dir / "hooks" / "unbound.py")
    targets = [t for t in targets if t.is_file()]
    hooks = doc.get("hooks")
    return any(_command_runs_our_hook(_command_of(hook)[1], targets, home)
               for _, _, hook in _iter_commands(hooks if isinstance(hooks, dict) else {}))


# Shell words after which the next word is the program a line runs.
_COMMAND_STARTERS = {";", "&&", "||", "|", "&", "then", "else", "do", "exec"}


def _command_runs_our_hook(command: str, targets: List[Path], home: Path) -> bool:
    """Whether any program the shell line runs is one of ``targets``: the first
    word, or one after a starter as in `if [ -f X ]; then exec X; fi`. Each
    program is resolved the way hooks_extractor resolves a hook's (env, NAME=,
    interpreters such as python3.11, ~ and $HOME for this user's home), plus
    the Windows `py -3` launcher setup writes. A path that is only an argument
    (`cat X`) is not a run."""
    if not command:
        return False
    try:
        # posix=False keeps each word's quotes, so a segment re-joined below
        # still expands (or doesn't) the way the shell would.
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return False
    wanted = {os.path.normcase(os.path.normpath(str(t))) for t in targets}
    starts = [0] + [i + 1 for i, w in enumerate(words) if w in _COMMAND_STARTERS]
    return any(_segment_runs(words[i:], wanted, home) for i in starts if i < len(words))


def _segment_runs(words: List[str], wanted: set, home: Path) -> bool:
    if os.path.basename(words[0].strip('"')).lower().removesuffix(".exe") == "py":
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
        words = ["python"] + words
    program = _program_path(" ".join(words), None, None, home)
    if program is None:
        return False
    return os.path.normcase(os.path.normpath(str(program))) in wanted or _is_unbound_binary(program)


def _is_unbound_binary(program: Path) -> bool:
    """An installed unbound-hook anywhere under /opt/unbound (current/ or a
    version dir), as setup's own matcher accepts."""
    root = os.path.normpath(str(UNBOUND_ROOT)) + os.sep
    path = os.path.normpath(str(program))
    return path.startswith(root) and os.path.basename(path) == "unbound-hook" and os.path.isfile(path)


# -- Codex ----------------------------------------------------------------------

def _codex_hooks_disabled(home: Path) -> Optional[bool]:
    """[features].hooks (alias codex_hooks), on by default. Requirements pin it
    over every config layer: MDM requirements, then the system requirements.toml;
    then MDM config, managed_config.toml and the user's config.toml."""
    system = platform.system()
    system_dir = CODEX_SYSTEM_DIRS.get(system)
    mdm = _read_plist(MANAGED_PREFERENCES_DIR / "com.openai.codex.plist") if system == "Darwin" else None
    if mdm is _UNREADABLE:
        return None
    mdm = mdm if isinstance(mdm, dict) else {}
    managed_config = (home / ".codex" if system == "Windows" else system_dir) / "managed_config.toml" \
        if system_dir else None
    layers = [_decode_base64(mdm.get("requirements_toml_base64")),
              _read_text(system_dir / "requirements.toml", system_dir) if system_dir else None,
              _decode_base64(mdm.get("config_toml_base64")),
              _read_text(managed_config, managed_config.parent) if managed_config else None,
              _read_text(home / ".codex" / "config.toml", home, follow_symlinks=True)]
    for text in layers:
        if text is _UNREADABLE:
            return None
        if text is None:
            continue
        enabled = _toml_hooks_feature(text)
        if enabled is _UNREADABLE:
            return None
        if enabled is not None:
            return not enabled
    return False


def _decode_base64(value):
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return _UNREADABLE


_FEATURES_TABLE = re.compile(r"^\s*\[\s*features\s*\]\s*$")
_ANY_TABLE = re.compile(r"^\s*\[")
_FEATURE_FLAG = re.compile(r"^\s*(hooks|codex_hooks)\s*=\s*(true|false)\s*(#.*)?$")


def _toml_hooks_feature(text: str):
    """True/False from [features] hooks (or codex_hooks), None when unset."""
    try:
        import tomllib
    except ImportError:
        return _toml_hooks_feature_by_line(text)
    try:
        features = tomllib.loads(text).get("features")
    except tomllib.TOMLDecodeError:
        return _UNREADABLE
    if not isinstance(features, dict):
        return None
    for key in ("hooks", "codex_hooks"):
        if isinstance(features.get(key), bool):
            return features[key]
    return None


def _toml_hooks_feature_by_line(text: str):
    """Python < 3.11 has no tomllib: read just the [features] table's flag lines."""
    in_features, found = False, {}
    for line in text.splitlines():
        if _ANY_TABLE.match(line):
            if line.count("[") != line.count("]"):
                return _UNREADABLE
            in_features = bool(_FEATURES_TABLE.match(line))
            continue
        match = _FEATURE_FLAG.match(line) if in_features else None
        if match:
            found[match.group(1)] = match.group(2) == "true"
    return found.get("hooks", found.get("codex_hooks"))


# -- VS Code --------------------------------------------------------------------

def _vscode_hooks_disabled(home: Path) -> Optional[bool]:
    """chat.useHooks false in the user's VS Code settings turns hooks off; unset is unknown."""
    parts = _VSCODE_USER_SETTINGS.get(platform.system())
    if parts is None:
        return None
    settings = _read_json(home.joinpath(*parts), home, follow_symlinks=True)
    value = settings.get("chat.useHooks") if isinstance(settings, dict) else None
    return (not value) if isinstance(value, bool) else None


# -- reading ----------------------------------------------------------------------

def _read_text(path: Path, root: Path, follow_symlinks: bool = False):
    """File text, None when absent, _UNREADABLE when present but not readable."""
    if not path.exists():
        return None
    read = read_rule_file_contained(path, root, allow_symlink=follow_symlinks, max_size=_MAX_POLICY_SIZE)
    if read is None or read[1]:
        return _UNREADABLE
    return read[0]


def _read_json(path: Path, root: Path, follow_symlinks: bool = False):
    text = _read_text(path, root, follow_symlinks)
    if text is None or text is _UNREADABLE:
        return text
    return _parse_json(text)


def _parse_json(text: str):
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = json.loads(_strip_trailing_commas(_strip_jsonc_comments(text)))
        except ValueError:
            return _UNREADABLE
    return data if isinstance(data, dict) else _UNREADABLE


def _read_plist(path: Path):
    if not path.exists():
        return None
    try:
        if path.stat().st_size > _MAX_POLICY_SIZE:
            return _UNREADABLE
        with open(path, "rb") as f:
            data = plistlib.load(f)
    except Exception:
        return _UNREADABLE
    return data if isinstance(data, dict) else _UNREADABLE
