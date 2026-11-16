"""Secret patterns checked on the device against Claude Code memory files."""
import re
from bisect import bisect_right
from itertools import accumulate
from typing import Dict, Iterator, List, Pattern, Tuple

# Mirrors the backend hardcoded_secrets rules: keep in sync, and bump RULES_VERSION on any change.
RULES_VERSION = 2

SECRET_RULES: Tuple[Tuple[str, Pattern, Tuple[Pattern, ...]], ...] = (
    (
        'SECRET-001',
        re.compile(r'(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}(?![A-Z0-9])'),
        (),
    ),
    (
        'SECRET-002',
        re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82})'),
        (),
    ),
    (
        'SECRET-003',
        re.compile(r'(?:sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{20,}|(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,})'),
        (),
    ),
    (
        'SECRET-004',
        re.compile(r'(?:xox[bpras]-[A-Za-z0-9\-]{24,}|xoxe\.xoxp-[0-9]+-[0-9]+-[0-9]+-[a-f0-9]+|xapp-[0-9]+-[A-Za-z0-9]+-[0-9]+-[A-Za-z0-9]+)'),
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
            r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\r?\n'
            r'(?:[A-Za-z0-9+/=]{20,}\r?\n){2,}'
            r'-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'
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
        re.compile(r'(?:mongodb|mysql|postgresql|postgres)://[^:@/\s]{1,100}:[^@\n]{1,200}@'),
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
            r'(?:'
            r'(?:password|passwd|pwd)\s*[=:]\s*["\'][^\'"\s]{8,}["\']'
            r'|(?:api[_-]?key|apikey)\s*[=:]\s*["\'][^\'"\s]{16,}["\']'
            r'|(?:secret|token|auth[_-]?token|access[_-]?token)\s*[=:]\s*["\'][^\'"\s]{16,}["\']'
            r')',
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
            r'(?:azure|cognitive|openai)[_-]?(?:api)?[_-]?key[\'"]?\s*[:=]\s*[\'"]?([a-f0-9]{32})[\'"]?',
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
            r'(?:password|passwd|pwd|passphrase|secret|token|api_key|apikey)[A-Za-z0-9_.-]{0,20}[ \t]*[=:][ \t]*'
            r'(?![$<{%*])(?=[^\s\'"]{0,200}[0-9])(?!(?:none|null|true|false|required|optional|string|changeme)(?![^\s\'"])|xxx)'
            r'[^\s\'"]{8,}',
            re.IGNORECASE
        ),
        (),
    ),
    (
        'SECRET-022',
        re.compile(r'(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,30}://[^\s:@/]*:[^\s/@]+@'),
        (),
    ),
    (
        'SECRET-023',
        re.compile(
            r'[?&](?:token|access_token|api_key|apikey|key|sig|signature|secret|password|x-amz-signature|x-goog-signature)'
            r'=[^&\s#]{8,}',
            re.IGNORECASE
        ),
        (),
    ),
)


def _rule_findings(rule: Tuple[str, Pattern, Tuple[Pattern, ...]], content: str,
                   line_offsets: List[int]) -> Iterator[Tuple[str, int]]:
    rule_id, pattern, _ = rule
    for match in pattern.finditer(content):
        yield rule_id, bisect_right(line_offsets, match.start())


def find_secrets(content: str) -> List[Dict]:
    """Return one {rule_id, line} finding (1-based line) per rule and line with a secret match."""
    lines = content.split('\n')
    line_offsets = list(accumulate((len(line) + 1 for line in lines[:-1]), initial=0))
    found = dict.fromkeys(finding for rule in SECRET_RULES for finding in _rule_findings(rule, content, line_offsets))
    return [{"rule_id": rule_id, "line": line} for rule_id, line in found]
