"""
Fingerprint extraction for MCP server identity.

⚠️ KEEP IN SYNC: this logic is mirrored in
  - the gateway: `ai-gateway/src/services/mcpFingerprint.ts` (PreToolUse hot path),
  - the backend: `ai-gateway-data/webapp/services/mcp_fingerprint.py` (ingest/recompute),
  - this discovery client copy (keys the local mcp-tools-cache.json), and
  - the device hooks: `setup/{claude-code,codex,copilot,augment}/hooks/unbound.py`
    and `setup/cursor/unbound.py` (embedded port that looks that cache up).
All copies MUST produce identical strings. Fingerprints identify server variants
and key the local cache; tool content hashes separately key scores in Redis.
Any change to fingerprint prefixes or extraction rules here requires the same
change in every port. Reviewers / the code-review bot: flag PRs that touch one
without the others.

The fingerprint is a stable identifier derived from an MCP server's technical
configuration (url, command, args). Two MCPServer records with the same fingerprint
refer to the same underlying service and share a single MCPServerMetadata record,
regardless of the user-configured name.

Prefix conventions encode the signal source so the fingerprint is self-describing:
    url:<host[:port]/path>       -> from the url field
    url-arg:<host[:port]/path>   -> from a URL embedded in args (e.g., mcp-remote proxies)
    git:<host/owner/repo>        -> from a git+ install spec in args (npx/uvx git installs)
    npm:<package>                -> from an npm package in args (@scoped, or bare under npx/npm/bunx)
    smithery:<server>            -> target passed to a registry-resolved Smithery CLI
    smithery-unverified:<server> -> target passed to a locally resolvable Smithery CLI
    nuget:<package>              -> package run by `dnx` or `dotnet tool exec`
    pypi:<package>               -> from a Python package run via uvx / uv / pipx
    docker:<image>               -> from `docker run ... <image>`
    script:<hash>                -> content hash of a local script (supplied by the client)
    bin:<name>                   -> basename of a bespoke local binary (args dropped)
    intellij:<name>              -> from an IntelliJ plugin-managed server (command == "builtin")
    claudeai:<name>              -> from a Claude.ai native integration (empty config, name starts with "claude.ai ")
    copilot-builtin:<name>       -> from a Copilot builtin MCP server (scope=copilot-builtin, bare config)
    claude-connector:<name>      -> from a Claude desktop OAuth remote connector. These arrive named by a
                                    per-registration UUID at runtime; the client hook resolves the real
                                    display name and tags the config scope="claude-connector".
    claude-builtin:<name>        -> from a Claude Code first-party built-in (computer-use,
                                    claude-in-chrome, claude-browser, claude-preview, claude-design,
                                    ccd-session, ccd-session-mgmt, ide). Runtime-provided, no config;
                                    every display-name spelling of one server maps to one identity.

`script:` is the only prefix that depends on a client-supplied value
(`script_hash`) rather than the config alone: the gateway never reads file
contents, so the hook computes the hash and sends it. When absent, a local
script run yields no fingerprint.

Returns None when no signal can be extracted -- the caller falls back to name-based lookup.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


CLAUDE_BUILTIN_PREFIX = 'claude-builtin:'

CLAUDE_CONNECTOR_SCOPE = 'claude-connector'

# Claude Code sanitizes display names into runtime names (non-alphanumerics -> '_'), so one
# server arrives under several spellings. chrome/browser/preview stay separate: different tools.
_CLAUDE_BUILTIN_NAMES = {
    'computer-use': 'computer-use',
    'claude-in-chrome': 'claude-in-chrome',
    'claude-for-chrome': 'claude-in-chrome',
    'claude-browser': 'claude-browser',
    'claude-preview': 'claude-preview',
    'claude-design': 'claude-design',
    'ccd-session': 'ccd-session',
    'ccd-session-mgmt': 'ccd-session-mgmt',
    'ide': 'ide',
}

_BUILTIN_NAME_SEPARATOR_RE = re.compile(r'[\s_]+')


def claude_builtin_identity(name):
    """Canonical built-in identity for a bare server name, or None."""
    key = _BUILTIN_NAME_SEPARATOR_RE.sub('-', (name or '').strip().lower())
    return _CLAUDE_BUILTIN_NAMES.get(key)

CLAUDEAI_NAME_PREFIX = 'claude.ai '
CLAUDEAI_ALLOWED_ADDITIONAL_DATA = ({}, {'scope': 'claudeai'})

# npm-package runners: the first positional arg is the package to run.
NPM_RUNNERS = frozenset({'npx', 'npm', 'bunx'})
SMITHERY_CLI_PACKAGES = frozenset({'@smithery/cli', 'smithery'})
SMITHERY_GLOBAL_FLAGS = frozenset({'--verbose', '--debug', '--json', '--table'})
# Sub-runners under npx/bunx that are not the package themselves (the real
# target -- usually a local script -- follows).
NPX_LOCAL_RUNNERS = frozenset({'tsx', 'ts-node'})
# npm/bunx subcommands that precede the actual package name.
NPM_SUBCOMMANDS = frozenset({'exec', 'run', 'run-script', 'x', 'create', 'init', 'install', 'i'})
# Python-package runners and the sub-commands that precede the package.
PYPI_RUNNERS = frozenset({'uvx', 'uv', 'pipx'})
PYPI_SUBCOMMANDS = frozenset({'run', 'tool', 'tool-run'})
NUGET_RUNNERS = frozenset({'dnx', 'dotnet'})

# Prompt Security's MCP proxy wraps the real server command after this token.
PROMPT_SECURITY_BASENAME = 'prompt_security_mcp'
PROMPT_SECURITY_ARGS_SENTINEL = '__args__'

# Language runtimes that execute a local script given as an arg. They never
# produce a `bin:` identity -- their script identity is the content hash.
# Keep in sync with _HOOK_SCRIPT_RUNTIMES in the hook files (setup/*/hooks/unbound.py).
RUNTIMES = frozenset({
    'node', 'nodejs', 'bun', 'deno', 'python', 'python2', 'python3', 'py',
    'ruby', 'dart', 'php', 'perl', 'rscript',
})

# Commands that have their own rule (or are runtimes) -- excluded from the
# catch-all `bin:` tier so they don't double-resolve.
BIN_SKIP_COMMANDS = (
    RUNTIMES | NPM_RUNNERS | PYPI_RUNNERS | NUGET_RUNNERS
    | frozenset({'docker', 'builtin', PROMPT_SECURITY_BASENAME})
)

# Basenames too generic to identify a product -- `bin:` skips these (they
# collide across unrelated servers).
GENERIC_BIN_NAMES = frozenset({
    'mcp-server', 'mcpserver', 'mcp', 'server', 'main', 'index', 'start', 'app',
    'run', 'cli', 'bin', 'tool', 'agent', 'my-command', 'my-mcp-server', 'node-repl',
})

# Shells / build orchestrators / generic launchers. Their basename is not a
# product identity -- the real server lives in the args (which `bin:` drops) or
# in a file they exec. They never produce a `bin:` fingerprint.
LAUNCHER_COMMANDS = frozenset({
    'sh', 'bash', 'zsh', 'fish', 'dash', 'ksh', 'cmd', 'powershell', 'pwsh',
    'env', 'cscript', 'wscript', 'make', 'mach', 'task', 'just',
})

# A command that is itself a local script file (run directly, not via a
# runtime). Its identity is the file contents, so it routes to the `script:`
# tier -- never `bin:`.
_SCRIPT_COMMAND_RE = re.compile(r'\.(sh|py|js|cjs|mjs|ts|tsx|rb|php|dart)$', re.IGNORECASE)


_LOCAL_PATH_EXT_RE = re.compile(r'\.(js|cjs|mjs|ts|tsx|py|rb|php|dart|sh|rs|go|jar)$', re.IGNORECASE)
_EXE_SUFFIX_RE = re.compile(r'\.(exe|cmd|bat|com)$')
_PLATFORM_SUFFIX_RE = re.compile(
    r'-(darwin|linux|windows|macos|win32|win)(-(arm64|x64|x86|amd64|aarch64))?$'
)


# Scheme default ports, dropped from the identity (mirrors JS URL semantics).
_DEFAULT_PORTS = {'http': 80, 'https': 443, 'ws': 80, 'wss': 443}


def _extract_url_identity(url_value: str) -> Optional[str]:
    """
    Normalize a URL into a stable identity string: `host[:port]/path`.

    The path is kept (not stripped) so multi-tenant proxy services like
    mintmcp.com / composio don't collapse into a single fingerprint when they
    actually serve different underlying services at different paths. Query and
    fragment are dropped (those typically carry session/auth params that vary
    per install).

    Host is lowercased, trailing slashes on path are stripped, empty paths
    normalize to an absent segment.
    """
    if not url_value or not isinstance(url_value, str):
        return None
    try:
        parsed = urlparse(url_value.strip())
    except ValueError:
        return None

    host = (parsed.hostname or '').lower()
    if not host:
        return None

    # Drop the scheme's default port so `https://h:443/x` and `https://h/x`
    # share one identity (matches JS `new URL().port`, which omits defaults).
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and _DEFAULT_PORTS.get((parsed.scheme or '').lower()) == port:
        port = None
    host_port = f'{host}:{port}' if port else host

    # Normalize path: drop trailing slashes, drop if empty or just "/"
    path = (parsed.path or '').rstrip('/')
    identity = f'{host_port}{path}' if path else host_port
    return identity


def _urls_in_args(args: List[str]) -> List[str]:
    return [
        a for a in args
        if isinstance(a, str) and (a.startswith('http://') or a.startswith('https://'))
    ]


def _command_base(command: Optional[str]) -> str:
    """Basename of a command, lowercased, with a Windows executable suffix dropped."""
    if not command:
        return ''
    base = re.split(r'[\\/]', command.strip())[-1]
    return _EXE_SUFFIX_RE.sub('', base.lower())


def _trusted_launcher_base(command: Optional[str]) -> str:
    token = _unquote(command).strip() if command else ''
    if '/' in token or '\\' in token:
        return ''
    return _command_base(token)


def _unquote(value: str) -> str:
    """Strip surrounding quotes some clients leave in arg values."""
    return value.strip('"\'')


def _looks_like_local_path(value: str) -> bool:
    """A path to a local file/script (not a package/identity)."""
    v = _unquote(value)
    if v.startswith('http://') or v.startswith('https://'):
        return False
    if v.startswith('@'):  # npm scope, not a path
        return False
    if v.startswith('git+'):
        return False
    if '${' in v:  # env-var path template
        return True
    if '/' in v or '\\' in v:
        return True
    return bool(_LOCAL_PATH_EXT_RE.search(v))


def _npm_package_from_args(args: List[str]) -> Optional[str]:
    for arg in args:
        if not isinstance(arg, str) or not arg.startswith('@'):
            continue
        return _registry_npm_package(arg)
    return None


def _registry_npm_package(spec: str) -> Optional[str]:
    token = _unquote(spec).strip()
    if token.startswith('@'):
        version_at = token.find('@', 1)
        package = token if version_at == -1 else token[:version_at]
        selector = None if version_at == -1 else token[version_at + 1:]
        package_pattern = r'@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*'
    else:
        package, separator, selector = token.partition('@')
        selector = selector if separator else None
        package_pattern = r'[a-z0-9][a-z0-9._-]*'
    if not re.fullmatch(package_pattern, package, re.IGNORECASE):
        return None
    if selector is not None and (
        selector.startswith('.')
        or not re.fullmatch(r'[a-z0-9*^~<>=.+_-]+', selector, re.IGNORECASE)
    ):
        return None
    return package.lower()


def _smithery_server_identity(value: str) -> Optional[str]:
    target = _unquote(value).strip()
    if target.startswith('@'):
        target = target[1:]
    pattern = r'[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?'
    return target.lower() if re.fullmatch(pattern, target, re.IGNORECASE) else None


def _is_registry_resolved_smithery_cli(value: str) -> bool:
    return _unquote(value).strip().lower() in {
        '@smithery/cli@latest',
        'smithery@latest',
    }


def _smithery_command_target(tokens: List[str]) -> Optional[str]:
    while tokens and str(tokens[0]).lower() in SMITHERY_GLOBAL_FLAGS:
        tokens = tokens[1:]
    if tokens and str(tokens[0]).lower() == 'mcp':
        tokens = tokens[1:]
        while tokens and str(tokens[0]).lower() in SMITHERY_GLOBAL_FLAGS:
            tokens = tokens[1:]
    if not tokens or str(tokens[0]).lower() != 'run':
        return None

    target = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not isinstance(token, str):
            return None
        lower = token.lower()
        if lower in {'--config', '--key'}:
            if (
                index + 1 >= len(tokens)
                or not isinstance(tokens[index + 1], str)
                or tokens[index + 1].startswith('-')
            ):
                return None
            index += 2
            continue
        if any(lower.startswith(f'{flag}=') for flag in ('--config', '--key')):
            if not token.partition('=')[2]:
                return None
            index += 1
            continue
        if token.startswith('-') or target is not None:
            return None
        target = _smithery_server_identity(token)
        if target is None:
            return None
        index += 1
    return target


def _smithery_run_target(
    args: List[str],
    command_base: str,
    launcher_trusted: bool,
) -> Optional[Tuple[str, bool]]:
    if command_base == 'smithery':
        target = _smithery_command_target(args)
        return (target, False) if target else None
    if command_base == 'cmd' and any(
        isinstance(arg, str) and re.search(r'[&|<>^\r\n]', arg)
        for arg in args
    ):
        return None

    for index, arg in enumerate(args):
        if (
            not isinstance(arg, str)
            or _registry_npm_package(arg) not in SMITHERY_CLI_PACKAGES
        ):
            continue
        prefix_tokens = []
        for prefix_arg in args[:index]:
            if not isinstance(prefix_arg, str):
                return None
            prefix_tokens.append(_unquote(prefix_arg).lower())

        def valid_runner_prefix(runner, tokens):
            safe_flags = {'-y', '--yes'}
            if runner in {'bunx', 'bun'}:
                safe_flags.update({'--bun', '--no-install', '--verbose', '--silent'})
            if runner == 'npm':
                safe_flags.add('--')
            if any(token.startswith('-') and token not in safe_flags for token in tokens):
                return False
            positional = [token for token in tokens if token not in safe_flags]
            if runner == 'npm':
                return tokens[-1:] == ['--'] and positional in (['exec'], ['x'])
            if runner == 'bun':
                return positional == ['x']
            return not positional

        if command_base in NPM_RUNNERS:
            if not valid_runner_prefix(command_base, prefix_tokens):
                return None
        elif command_base == 'bun':
            if not valid_runner_prefix(command_base, prefix_tokens):
                return None
        elif command_base == 'cmd':
            runner_positions = [
                offset for offset, token in enumerate(prefix_tokens)
                if _trusted_launcher_base(token) in NPM_RUNNERS | {'bun'}
            ]
            if len(runner_positions) != 1:
                return None
            runner_index = runner_positions[0]
            shell_prefix = prefix_tokens[:runner_index]
            if '/c' not in shell_prefix or any(
                token not in {'/c', '/d', '/s'} for token in shell_prefix
            ):
                return None
            if not valid_runner_prefix(
                _trusted_launcher_base(prefix_tokens[runner_index]),
                prefix_tokens[runner_index + 1:],
            ):
                return None
        else:
            return None
        target = _smithery_command_target(args[index + 1:])
        if target is None:
            return None
        registry_resolved = (
            launcher_trusted
            and command_base in {'npx', 'npm'}
            and _is_registry_resolved_smithery_cli(arg)
        )
        return target, registry_resolved
    return None


def _nuget_package(base: str, args: List[str]) -> Optional[str]:
    if base == 'dnx':
        tokens = args
    elif base == 'dotnet' and args and str(args[0]).lower() == 'dnx':
        tokens = args[1:]
    elif (
        base == 'dotnet'
        and len(args) >= 2
        and str(args[0]).lower() == 'tool'
        and str(args[1]).lower() in {'exec', 'execute'}
    ):
        tokens = args[2:]
    else:
        return None

    if any(
        isinstance(arg, str)
        and (
            arg.lower() == '--configfile'
            or arg.lower().startswith('--configfile=')
            or arg.lower().startswith('--configfile:')
            or arg.lower() == '--add-source'
            or arg.lower().startswith('--add-source=')
            or arg.lower().startswith('--add-source:')
        )
        for arg in tokens
    ):
        return None

    source_flags = {'--source', '-s'}
    source_seen = False
    for index, arg in enumerate(tokens):
        if not isinstance(arg, str):
            continue
        if arg == '--':
            break
        value = None
        if arg.lower() in source_flags:
            value = tokens[index + 1] if index + 1 < len(tokens) else None
        elif any(
            arg.lower().startswith((f'{flag}=', f'{flag}:'))
            for flag in source_flags
        ):
            value = re.split(r'[=:]', arg, maxsplit=1)[1]
        if value is None:
            continue
        source_seen = True
        if not isinstance(value, str):
            return None
        parsed = urlparse(_unquote(value))
        if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {
            'api.nuget.org', 'nuget.org', 'www.nuget.org',
        }:
            return None
    if not source_seen:
        return None

    value_options = {
        '--version', '--framework', '--arch', '-a', '--verbosity', '-v',
        '--configfile', '--source', '-s',
    }
    flag_options = {
        '--prerelease', '--allow-roll-forward', '--ignore-failed-sources',
        '--interactive', '--yes', '-y', '--disable-parallel', '--no-cache',
        '--no-http-cache',
    }
    candidate = None
    version = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == '--':
            if candidate is None:
                return None
            break
        if not isinstance(token, str):
            return None
        lower = token.lower()
        option = re.split(r'[=:]', lower, maxsplit=1)[0]
        has_attached_value = len(option) < len(token)
        if option in value_options:
            if option == '--version':
                value = token[len(option) + 1:] if has_attached_value else (
                    tokens[index + 1] if index + 1 < len(tokens) else None
                )
                if not isinstance(value, str) or version is not None:
                    return None
                version = _unquote(value)
            index += 1 if has_attached_value else 2
            continue
        if lower in flag_options:
            index += 1
            continue
        if token.startswith('-'):
            return None
        if candidate is not None:
            return None
        candidate = token
        index += 1
    if not isinstance(candidate, str):
        return None
    package, separator, inline_version = _unquote(candidate).partition('@')
    if separator:
        if version is not None:
            return None
        version = inline_version
    if not version or not re.fullmatch(
        r'(?:\*|[0-9][a-z0-9*.+_-]*)', version, re.IGNORECASE,
    ):
        return None
    package = package.lower()
    return package if re.fullmatch(r'[a-z0-9][a-z0-9._-]*', package) else None


def _normalize_pypi(pkg: str) -> str:
    """Strip a version spec from a Python requirement: name==1, name>=1, name@1."""
    return re.split(r'[=<>@~!]', _unquote(pkg))[0]


def _git_identity(spec: str) -> Optional[str]:
    """git+https://github.com/owner/repo(.git)(@ref) -> github.com/owner/repo"""
    s = _unquote(spec)
    if s.startswith('git+'):
        s = s[4:]
    s = re.sub(r'^(https?|ssh|git)://', '', s)
    s = re.sub(r'^[^/]*@', '', s).replace(':', '/', 1)
    s = re.sub(r'\.git(@.*)?$', '', s)
    s = re.sub(r'@[^/]*$', '', s)
    parts = [p for p in s.split('/') if p]
    if len(parts) < 3:
        return None
    return '/'.join(parts[:3]).lower()


def _git_from_args(args: List[str]) -> Optional[str]:
    for arg in args:
        if not isinstance(arg, str):
            continue
        t = _unquote(arg)
        is_git = (
            t.startswith('git+')
            or t.startswith('git@')
            or (re.match(r'^(https?|ssh)://', t) is not None
                and re.search(r'(github\.com|gitlab\.com|bitbucket\.org)', t) is not None)
        )
        if not is_git:
            continue
        identity = _git_identity(t)
        if identity:
            return identity
    return None


def _package_from_runner_args(args: List[str], skip: frozenset) -> Optional[str]:
    """First package-looking arg under a runner, skipping flags, the runner's own
    sub-tokens, and bailing on a local-path arg (that's a script, not a package)."""
    for arg in args:
        if not isinstance(arg, str) or arg.startswith('-'):
            continue
        t = _unquote(arg)
        if t in skip:
            continue
        if _looks_like_local_path(t):
            return None
        return t
    return None


def _npm_runner_invocation(
    command_base: str,
    args: List[str],
) -> Optional[Tuple[str, List[str]]]:
    if command_base in NPM_RUNNERS:
        return command_base, args
    if command_base == 'bun':
        if args and isinstance(args[0], str) and _unquote(args[0]).lower() == 'x':
            return command_base, args[1:]
        return None
    if command_base != 'cmd' or any(
        not isinstance(arg, str) or re.search(r'[&|<>^\r\n]', arg)
        for arg in args
    ):
        return None

    runner_positions = [
        index for index, arg in enumerate(args)
        if _trusted_launcher_base(arg) in NPM_RUNNERS | {'bun'}
    ]
    if len(runner_positions) != 1:
        return None
    runner_index = runner_positions[0]
    shell_prefix = [_unquote(arg).lower() for arg in args[:runner_index]]
    if '/c' not in shell_prefix or any(
        token not in {'/c', '/d', '/s'} for token in shell_prefix
    ):
        return None

    runner = _trusted_launcher_base(args[runner_index])
    nested_args = args[runner_index + 1:]
    if runner == 'bun':
        if not nested_args or _unquote(nested_args[0]).lower() != 'x':
            return None
        nested_args = nested_args[1:]
    return runner, nested_args


def _npm_package_before_smithery(runner: str, args: List[str]) -> Optional[str]:
    safe_flags = {'-y', '--yes', '--'}
    if runner in {'bun', 'bunx'}:
        safe_flags.update({'--bun', '--no-install', '--verbose', '--silent'})
    npm_command_seen = runner != 'npm'
    candidate = None
    for arg in args:
        if not isinstance(arg, str):
            return None
        if _registry_npm_package(arg) in SMITHERY_CLI_PACKAGES:
            break
        token = _unquote(arg)
        lower = token.lower()
        if token.startswith('-'):
            if lower not in safe_flags:
                return None
            continue
        if not npm_command_seen:
            if lower not in {'exec', 'x'}:
                return None
            npm_command_seen = True
            continue
        if _trusted_launcher_base(token) in NPM_RUNNERS | {'bun'}:
            return None
        if candidate is not None or _looks_like_local_path(token):
            return None
        candidate = token
    return candidate if npm_command_seen else None


# `docker run` BOOLEAN flags — the closed, stable set that consumes NO value.
# Everything else that looks like a flag is treated as value-taking, so an
# unknown or newly-added value flag can never leak its value as the image: it
# fails toward no fingerprint rather than a wrong one. This is the reliable axis
# (the value-flag set is open-ended and grows with docker; the boolean set does
# not). `--flag=value` and attached short values (`-eKEY`) are self-contained.
DOCKER_BOOLEAN_FLAGS = frozenset({
    '-i', '--interactive', '-t', '--tty', '-d', '--detach', '-P', '--publish-all',
    '--rm', '--init', '--privileged', '--read-only', '--no-healthcheck',
    '--oom-kill-disable', '--disable-content-trust', '--sig-proxy', '-q', '--quiet',
})
_DOCKER_SHORT_BOOLEANS = set('itdPq')       # for combined forms: -it, -itd
_DOCKER_SHORT_VALUES = set('evpwulmhca')    # for attached forms: -eKEY, -p8080

# A docker image candidate, tag and digest already stripped. Repository names are
# lowercase (docker rejects `docker run FOO`), so this rejects any leaked
# uppercase value as a final backstop.
_DOCKER_IMAGE_REF_RE = re.compile(r'[a-z0-9][a-z0-9._:/-]*')
_DOCKER_DIGEST_RE = re.compile(r'@[A-Za-z0-9]+:[A-Fa-f0-9]+$')  # @sha256:<hex>


def _is_docker_image_ref(candidate: str) -> bool:
    return bool(_DOCKER_IMAGE_REF_RE.fullmatch(candidate))


def _docker_flag_consumes_value(arg: str) -> bool:
    """True when this flag takes the FOLLOWING token as its value (so that token
    is not the image). Boolean flags and self-contained forms return False;
    anything unrecognized is assumed value-taking (fail toward null)."""
    if '=' in arg:
        return False                        # --flag=value / -e=value (attached)
    if arg in DOCKER_BOOLEAN_FLAGS:
        return False
    if arg.startswith('--'):
        return True                         # any other long flag: value-taking
    letters = arg[1:]                        # short flag(s): -x or bundle -xyz
    if letters and all(c in _DOCKER_SHORT_BOOLEANS for c in letters):
        return False                        # combined booleans, e.g. -it, -itd
    if len(letters) > 1 and letters[0] in _DOCKER_SHORT_VALUES:
        return False                        # attached value, e.g. -eKEY, -p8080
    return True                             # -e / -p (separate value) or unknown


def _normalize_docker_image(arg: str) -> str:
    image = _unquote(arg)
    image = _DOCKER_DIGEST_RE.sub('', image)     # drop @sha256:<digest>
    return re.sub(r':[^/]+$', '', image)         # drop :tag, keep registry/repo


def _docker_image_from_args(args: List[str]) -> Optional[str]:
    # Skip each value flag's next token; the first bare, lowercase image-ref-shaped
    # token is the image. Unknown flags are assumed value-taking, so a leaked value
    # never becomes the fingerprint.
    if 'run' not in args:
        return None
    run_idx = args.index('run')
    skip_next = False
    for arg in args[run_idx + 1:]:
        if not isinstance(arg, str):
            continue
        if skip_next:
            skip_next = False
            continue
        if arg.startswith('-'):
            if _docker_flag_consumes_value(arg):
                skip_next = True
            continue
        image = _normalize_docker_image(arg)
        if _is_docker_image_ref(image):
            return image
    return None


def _command_is_script_file(command: str) -> bool:
    """True when the command is itself a local script file (e.g. `.../bin.sh`)."""
    base = re.split(r'[\\/]', command.strip())[-1]
    return bool(_SCRIPT_COMMAND_RE.search(base))


def _normalize_bin(command: str) -> Optional[str]:
    """Basename of a bespoke binary, normalized for cross-platform collapse. Drops
    the path, executable suffix, and platform/arch suffix; None when generic."""
    b = re.split(r'[\\/]', command.strip())[-1].lower()
    b = _EXE_SUFFIX_RE.sub('', b)
    b = _PLATFORM_SUFFIX_RE.sub('', b)
    b = b.strip(' -_')
    if not b or b in GENERIC_BIN_NAMES:
        return None
    return b


def compute_fingerprint(
    name: Optional[str],
    command: Optional[str],
    url: Optional[str],
    args: Optional[List[str]],
    additional_data: Optional[Dict[str, Any]],
    script_hash: Optional[str] = None,
) -> Optional[str]:
    """
    Derive a stable fingerprint for an MCP server.

    The function is a priority chain: the first signal that yields a result wins.
    `script_hash`, when provided, is the client-computed content hash of a local
    script (the gateway/control plane cannot read the file itself).
    Returns None when no signal is extractable.
    """
    safe_name = name or ''
    safe_args = args or []
    safe_additional_data = additional_data or {}
    base = _command_base(command)
    launcher_base = _trusted_launcher_base(command)

    # 0. Prompt Security proxy: the real server command follows `__args__`.
    #    Unwrap and fingerprint the inner command instead of the wrapper.
    if base == PROMPT_SECURITY_BASENAME and PROMPT_SECURITY_ARGS_SENTINEL in safe_args:
        idx = safe_args.index(PROMPT_SECURITY_ARGS_SENTINEL)
        if idx + 1 < len(safe_args):
            inner = safe_args[idx + 1:]
            inner_cmd = inner[0] if inner else None
            inner_url = inner_cmd if inner_cmd and inner_cmd.startswith(('http://', 'https://')) else None
            return compute_fingerprint(
                name=safe_name,
                command=None if inner_url else inner_cmd,
                url=inner_url,
                args=inner[1:],
                additional_data=safe_additional_data,
                script_hash=script_hash,
            )

    # Claude desktop OAuth remote connector. Named by a per-registration UUID at
    # runtime; the client hook resolves the display name and tags the config
    # scope="claude-connector" so every instance of e.g. "Gmail" groups by name.
    # This wins over the url branch below: the connector carries a per-registration
    # url, but the device sweep that seeds the keeper omits it, so fingerprinting
    # by url here would never match claude-connector:<name>.
    if safe_additional_data.get('scope') == CLAUDE_CONNECTOR_SCOPE and safe_name:
        return f'claude-connector:{safe_name.lower()}'

    if (safe_additional_data.get('scope') == 'copilot-builtin' and safe_name
            and not command and not url and not safe_args):
        return f'copilot-builtin:{safe_name.lower()}'

    # First-party built-ins arrive as a bare name (no command/url/args); collapse
    # separator variants to one identity so aliases share a fingerprint.
    if not command and not url and not safe_args:
        builtin = claude_builtin_identity(safe_name)
        if builtin:
            return f'{CLAUDE_BUILTIN_PREFIX}{builtin}'

    # 1. url field -> url:<host[:port]/path>
    if url:
        identity = _extract_url_identity(url)
        if identity:
            return f'url:{identity}'

    nuget_package = _nuget_package(launcher_base, safe_args)
    if nuget_package:
        return f'nuget:{nuget_package}'

    smithery_match = _smithery_run_target(
        safe_args,
        base,
        launcher_trusted=bool(launcher_base),
    )
    if smithery_match:
        smithery_target, registry_resolved = smithery_match
        prefix = 'smithery' if registry_resolved else 'smithery-unverified'
        return f'{prefix}:{smithery_target}'
    if base == 'smithery':
        return None
    first_scoped_package = _npm_package_from_args(safe_args)
    runner_package = None
    runner_invocation = _npm_runner_invocation(launcher_base, safe_args)
    if runner_invocation is not None:
        runner, runner_args = runner_invocation
        if any(
            isinstance(arg, str)
            and _registry_npm_package(arg) in SMITHERY_CLI_PACKAGES
            for arg in runner_args
        ):
            candidate = _npm_package_before_smithery(runner, runner_args)
        else:
            candidate = _package_from_runner_args(
                runner_args,
                NPX_LOCAL_RUNNERS | NPM_SUBCOMMANDS | NPM_RUNNERS | RUNTIMES,
            )
        runner_package = _registry_npm_package(candidate) if candidate else None
    smithery_index = next((
        index for index, arg in enumerate(safe_args)
        if isinstance(arg, str)
        and _registry_npm_package(arg) in SMITHERY_CLI_PACKAGES
    ), None)
    first_scoped_index = next((
        index for index, arg in enumerate(safe_args)
        if isinstance(arg, str) and arg.startswith('@')
    ), None)
    if (
        smithery_index is not None
        and (
            first_scoped_index is None
            or first_scoped_index >= smithery_index
        )
    ):
        if launcher_base in NPM_RUNNERS | {'bun', 'cmd'}:
            if not runner_package or runner_package in SMITHERY_CLI_PACKAGES:
                return None
        first_scoped_package = None

    # 2. URLs inside args -> url-arg:<identity> (only if all URLs resolve to a single identity)
    url_args = _urls_in_args(safe_args)
    if url_args:
        identities = {_extract_url_identity(u) for u in url_args}
        identities.discard(None)
        if len(identities) == 1:
            return f'url-arg:{next(iter(identities))}'
        if len(identities) > 1:
            logger.warning(
                "MCP fingerprint ambiguity (multiple URLs in args) for server '%s'. "
                "identities=%s.",
                name, identities,
            )
            return None

    # 3. git+ install spec in args (npx/uvx git installs)
    git = _git_from_args(safe_args)
    if git:
        return f'git:{git}'

    # 4. @scoped npm package anywhere in args (command-agnostic, original rule)
    if first_scoped_package:
        return f'npm:{first_scoped_package}'

    # 5. npm package run via npx / npm / bunx (bare or quoted-scoped)
    if runner_package:
        return f'npm:{runner_package}'

    # 6. Python package run via uvx / uv / pipx
    if base in PYPI_RUNNERS:
        pkg = _package_from_runner_args(safe_args, PYPI_SUBCOMMANDS)
        if pkg:
            return f'pypi:{_normalize_pypi(pkg)}'

    # 7. docker run <image> (skip `docker mcp ...`, the Docker MCP gateway CLI)
    if base == 'docker' and (not safe_args or safe_args[0] != 'mcp'):
        image = _docker_image_from_args(safe_args)
        if image:
            return f'docker:{image}'

    # 8. IntelliJ plugin-managed server. Parser still checks literal "builtin"
    # (that's what coding-discovery-tool/.../jetbrains/mcp_config_extractor.py writes);
    # the prefix is intellij: for accurate semantic labeling.
    if command == 'builtin' and safe_name:
        return f'intellij:{safe_name.lower()}'

    # 9. Claude.ai native integration. Two name forms arrive: the hook-resolved
    # display ("claude.ai Atlassian") and the raw runtime key
    # ("claude_ai_Atlassian") when hook resolution missed — e.g. the first-time
    # `authenticate` call, before the connector is in claudeAiMcpEverConnected.
    # Reconstruct the display form from the raw key so both fingerprint the same.
    if (
        not command
        and not safe_args
        and safe_additional_data in CLAUDEAI_ALLOWED_ADDITIONAL_DATA
    ):
        if safe_name.startswith(CLAUDEAI_NAME_PREFIX):
            return f'claudeai:{safe_name.lower()}'
        raw_key = re.fullmatch(r'claude_ai_(.+)', safe_name)
        if raw_key:
            rest = re.sub(r'_+', ' ', raw_key.group(1)).strip().lower()
            if rest:
                return f'claudeai:{CLAUDEAI_NAME_PREFIX}{rest}'

    # (Claude desktop OAuth remote connector is handled above the url branch —
    # scope="claude-connector" groups by name regardless of the per-registration url.)

    # 11. Local script identified by client-supplied content hash. Covers both
    #     runtime+file (`node x.js`) and a script run directly (`.../bin.sh`).
    #     Ignore empty / punctuation-only values (e.g. "", "/", "///") -> None.
    clean_hash = (script_hash or '').strip()
    if re.fullmatch(r'[a-f0-9]{64}', clean_hash, re.IGNORECASE):
        return f'script:{clean_hash.lower()}'

    # 12. Bespoke local binary -- basename only, args dropped (they carry
    #     per-user paths/ids that would explode cardinality). Skips runtimes,
    #     launchers/shells, and script files (those are script-tier identities).
    if (
        command
        and base not in BIN_SKIP_COMMANDS
        and base not in LAUNCHER_COMMANDS
        and not _command_is_script_file(command)
    ):
        bin_name = _normalize_bin(command)
        if bin_name:
            return f'bin:{bin_name}'

    return None
