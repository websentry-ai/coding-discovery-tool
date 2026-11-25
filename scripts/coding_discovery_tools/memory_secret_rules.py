"""Secret patterns for Claude Code memory files."""
import re
from bisect import bisect_right
from itertools import accumulate
from typing import Dict, Iterator, List, Pattern, Tuple

# Bump RULES_VERSION on any change; a `value` group marks the secret inside a match, none means the whole match.
RULES_VERSION = 6

# A quoted value that is a placeholder, a shell variable or a template, not a secret.
_PLACEHOLDER = (
    r'(?![<{])(?!\$[({]|\$(?-i:[A-Z_][A-Z0-9_]*)["\'])(?![*xX]+["\'])'
    r'(?!(?:changeme|password|example|placeholder|redacted)["\'])(?!your[_-][^\'"\s]*["\'])'
)
# A URL password that is a bare variable reference ($VAR, ${VAR}, ${{ ctx }}, $(cmd)), not a secret; ${VAR:-literal} stays flagged.
_URL_VAR_PASSWORD = (
    r'(?!\$[A-Z_][A-Z0-9_]*@|\$\{(?:\{[^@\n}]{1,100}\}|[A-Za-z_][A-Za-z0-9_]{0,99})\}@|\$\([^@\n]{1,100}\)@)'
)
# A key that names a setting about the secret (its name, URL, TTL...) rather than holding it.
_DESCRIPTOR_KEY = (
    r'(?![A-Za-z0-9_.-]{0,20}?(?:[_.-]|(?-i:(?<=[a-z0-9])(?=[A-Z])))'
    r'(?:name|url|uri|arn|path|file|dir|ttl|ttl_ms|expiry|expires|expires_at|age|days|seconds|timeout|limit|attempts|'
    r'length|count|type|id|hashers?|endpoint|header|policy|prefix|format|version|field|label)["\']?[ \t]*[=:])'
)
# A value that is a URL, an ARN or a path under a well-known root rather than secret material.
_NON_SECRET_VALUE = (
    r'(?![A-Za-z][A-Za-z0-9+.-]*://|arn:|~/|\./|\.\./|[A-Za-z]:\\'
    r'|/(?:Users|home|root|var|etc|tmp|opt|usr|run|mnt|srv|private|app|data|Volumes|workspaces?)/)'
)
# The rest of a key after its keyword, the separator, and the start of a value that can hold a secret.
_KEY_TAIL = (
    _DESCRIPTOR_KEY + r's?(?:(?:access)?key)?(?:(?:[_.-]|(?-i:[A-Z0-9]))[A-Za-z0-9_.-]{0,19})?["\']?[ \t]*[=:][ \t]*'
    r'(?![$<{%*])' + _NON_SECRET_VALUE
)

SECRET_RULES: Tuple[Tuple[str, Pattern, Tuple[Pattern, ...]], ...] = (
    (
        'SECRET-001',
        re.compile(r'(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)(?!X{16})[0-9A-Z]{16}(?![A-Z0-9])'),
        (),
    ),
    (
        'SECRET-002',
        re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82})'),
        (),
    ),
    (
        'SECRET-003',
        re.compile(
            r'(?:sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{20,}'
            r'|(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,})'
        ),
        (),
    ),
    (
        'SECRET-004',
        re.compile(
            r'(?:xox[bpras]-[A-Za-z0-9\-]{24,}|xoxe\.xoxp-[0-9]+-[0-9]+-[0-9]+-[a-f0-9]+'
            r'|xapp-[0-9]+-[A-Za-z0-9]+-[0-9]+-[A-Za-z0-9]+)'
        ),
        (),
    ),
    (
        'SECRET-005',
        re.compile(r'glpat-[A-Za-z0-9_\-]{20,}'),
        (),
    ),
    (
        'SECRET-006',
        re.compile(r'npm_[A-Za-z0-9]{36}'),
        (),
    ),
    (
        'SECRET-007',
        re.compile(r'sk-ant-(?:api\d{2}-)?[A-Za-z0-9_-]{40,}'),
        (),
    ),
    (
        'SECRET-008',
        re.compile(
            r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n'
            r'(?:[A-Za-z-]{1,40}: [^\n]{0,200}\n|\r?\n){0,5}'
            r'(?:[A-Za-z0-9+/=]{20,}\r?\n)+(?:[A-Za-z0-9+/=]{1,19}\r?\n)?'
            r'-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'
        ),
        (),
    ),
    (
        'SECRET-009',
        re.compile(r'AIza[A-Za-z0-9_-]{35}'),
        (),
    ),
    (
        'SECRET-010',
        re.compile(r'(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
        (),
    ),
    (
        'SECRET-011',
        re.compile(
            r'(?:mongodb|mysql|postgresql|postgres)://[^:@/\s]{1,100}:' + _URL_VAR_PASSWORD
            + r'(?P<value>[^@\n]{1,200})@'
        ),
        (),
    ),
    (
        'SECRET-012',
        re.compile(r'hf_[A-Za-z0-9]{34}'),
        (),
    ),
    (
        'SECRET-013',
        re.compile(r'SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}'),
        (),
    ),
    (
        'SECRET-014',
        re.compile(r'pypi-[A-Za-z0-9_-]{100,}'),
        (),
    ),
    (
        'SECRET-015',
        re.compile(r'https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{24}'),
        (),
    ),
    (
        'SECRET-016',
        re.compile(
            r'(?:(?P<short>password|passwd|pwd)|api[_-]?key|apikey|secret|token|auth[_-]?token|access[_-]?token)'
            r'["\']?\s*[=:]\s*["\']' + _PLACEHOLDER + r'(?P<value>(?(short)[^\'"\s]{8,}|[^\'"\s]{16,}))["\']',
            re.IGNORECASE
        ),
        (),
    ),
    (
        'SECRET-017',
        re.compile(r'SK[a-f0-9]{32}'),
        (),
    ),
    (
        'SECRET-018',
        re.compile(r'(?<![A-Za-z0-9])[MN][A-Za-z0-9]{23,100}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,100}'),
        (),
    ),
    (
        'SECRET-019',
        re.compile(
            r'(?:azure|cognitive|openai)[_-]?(?:api)?[_-]?key[\'"]?\s*[:=]\s*[\'"]?(?P<value>[a-f0-9]{32})[\'"]?',
            re.IGNORECASE
        ),
        (),
    ),
    (
        'SECRET-020',
        re.compile(r'do[op]_v1_[a-f0-9]{64}'),
        (),
    ),
    (
        'SECRET-021',
        re.compile(
            r'(?:(?:password|passwd|pwd|passphrase)' + _KEY_TAIL
            + r'|(?:api_key|apikey)' + _KEY_TAIL + r'(?=[^\s\'"]{0,200}[A-Za-z]|[^\s\'"]{20,})'
            + r'|(?:secret|token)' + _KEY_TAIL + r'(?=[^\s\'"]{0,200}[A-Za-z]|[^\s\'"]{20,})'
            # Only under a token or secret name: a whole dotted or camelCase call, or one holding (), is code.
            r'(?!(?-i:(?:[a-z_][\w.]{0,60}[.A-Z][\w.]{0,60}|[a-z_][\w.]{0,60}(?=\([\w.,()]{0,200}\(\)))'
            # The value must end at the closing ")", or at a "," where whitespace cut a call such as getPart(1, true).
            r'\((?:[\w.,()]{0,200}\)[`,;]{0,2}|[\w.()]{0,200},)(?![^\s\'"]))))'
            r'(?=[^\s\'"]{0,200}[0-9])'
            r'(?!(?:none|null|true|false|required|optional|string|changeme)(?![^\s\'"])|xxx)'
            r'(?P<value>[^\s\'"]{8,})',
            re.IGNORECASE
        ),
        (),
    ),
    (
        'SECRET-022',
        re.compile(r'(?<=://)[^\s:@/]*:' + _URL_VAR_PASSWORD + r'(?P<value>[^\s/@]+)@'),
        (),
    ),
    (
        'SECRET-023',
        re.compile(
            r'[?&](?:(?P<plain>token|access_token|api_key|apikey|sig|signature|secret|password|x-amz-signature'
            r'|x-goog-signature)|key)=(?P<value>(?(plain)[^&\s#]{8,}|(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}'
            r'-[0-9a-f]{12}|(?=[A-Za-z0-9_]{0,200}[0-9])[A-Za-z0-9_]{20,}(?![A-Za-z0-9_-])'
            r'|(?-i:(?=[A-Za-z0-9_-]{0,200}[0-9])(?=[A-Za-z0-9_-]{0,200}[a-z])(?=[A-Za-z0-9_-]{0,200}[A-Z]))'
            r'[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-]))))',
            re.IGNORECASE
        ),
        (),
    ),
    (
        'SECRET-024',
        re.compile(r'\b(?i:bearer)[ \t]+(?=[A-Za-z0-9._~+/=-]{0,200}[0-9])(?P<value>[A-Za-z0-9._~+/=-]{20,})'),
        (),
    ),
)


def _rule_findings(rule: Tuple[str, Pattern, Tuple[Pattern, ...]], content: str,
                   line_offsets: List[int]) -> Iterator[Dict]:
    rule_id, pattern, _ = rule
    for match in pattern.finditer(content):
        start, end = match.span()
        value_start, value_end = match.span('value') if 'value' in pattern.groupindex else (-1, -1)
        if value_start < 0 or value_start >= value_end:
            value_start, value_end = start, end
        yield {"rule_id": rule_id, "line": bisect_right(line_offsets, start), "start": start, "end": end,
               "value_start": value_start, "value_end": value_end}


def find_secrets(content: str) -> List[Dict]:
    """Return one finding per secret match, sorted by start: rule_id, 1-based line, and str-index spans."""
    lines = content.split('\n')
    line_offsets = list(accumulate((len(line) + 1 for line in lines[:-1]), initial=0))
    found = [finding for rule in SECRET_RULES for finding in _rule_findings(rule, content, line_offsets)]
    return sorted(found, key=lambda finding: finding["start"])
