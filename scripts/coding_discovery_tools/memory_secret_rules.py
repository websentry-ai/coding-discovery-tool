"""Secret patterns checked on the device against Claude Code memory files."""
import re
from bisect import bisect_right
from functools import lru_cache
from itertools import accumulate
from typing import Dict, Iterator, List, Pattern, Tuple

# Mirrors the backend hardcoded_secrets rules: keep in sync, and bump RULES_VERSION on any change.
RULES_VERSION = 1

SECRET_RULES: Tuple[Tuple[str, Pattern, Tuple[Pattern, ...]], ...] = (
    (
        'SECRET-001',
        re.compile(r'(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}(?![A-Z0-9])'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder|documentation|(?:AKIA|ASIA)(?:EXAMPLE|SAMPLE|TEST|FAKE|XXXX))', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-002',
        re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82})'),
        (
            re.compile(r'(?:gh[pousr]_xxxx|gh[pousr]_example|gh[pousr]_your|gh[pousr]_REPLACE|github_pat_xxxx|github_pat_example)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-003',
        re.compile(r'(?:sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{20,}|(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,})'),
        (
            re.compile(r'(?:sk-xxxx|sk-your|sk-example|sk-\.{3,}|sk-proj-xxxx|sk-svcacct-xxxx|sk_live_xxxx|sk_test_xxxx|pk_live_xxxx|pk_test_xxxx)', re.IGNORECASE),
            re.compile(r'(?:YOUR_|REPLACE_|INSERT_|CHANGE_|EXAMPLE_|PLACEHOLDER)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-004',
        re.compile(r'(?:xox[bpras]-[A-Za-z0-9\-]{24,}|xoxe\.xoxp-[0-9]+-[0-9]+-[0-9]+-[a-f0-9]+|xapp-[0-9]+-[A-Za-z0-9]+-[0-9]+-[A-Za-z0-9]+)'),
        (
            re.compile(r'(?:xox[bpras]-xxxx|xox[bpras]-example|xox[bpras]-your|xapp-xxxx|xapp-example)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-005',
        re.compile(r'glpat-[A-Za-z0-9_\-]{20,}'),
        (
            re.compile(r'glpat-xxxx|glpat-example|glpat-your', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-006',
        re.compile(r'npm_[A-Za-z0-9]{36}'),
        (
            re.compile(r'npm_xxxx|npm_example|npm_your', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-007',
        re.compile(r'sk-ant-(?:api\d{2}-)?[A-Za-z0-9_-]{40,}'),
        (
            re.compile(r'(?:sk-ant-xxxx|sk-ant-example|sk-ant-your|sk-ant-REPLACE)', re.IGNORECASE),
            re.compile(r'(?:YOUR_|REPLACE_|INSERT_|CHANGE_|EXAMPLE_|PLACEHOLDER)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-008',
        re.compile(
            r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\r?\n'
            r'(?:[A-Za-z0-9+/=]{20,}\r?\n){2,}'
            r'-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'
        ),
        (
            re.compile(r'(?i)example'),
            re.compile(r'(?i)test'),
            re.compile(r'(?i)demo'),
            re.compile(r'(?i)sample'),
            re.compile(r'(?i)fake'),
            re.compile(r'(?i)placeholder'),
            re.compile(r'(?i)open.?source.?check'),
        ),
    ),
    (
        'SECRET-009',
        re.compile(r'AIza[A-Za-z0-9_-]{35}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder|your|REPLACE|INSERT)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-010',
        re.compile(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder|your_jwt|<jwt|jwt_token_here)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-011',
        re.compile(r'(?:mongodb|mysql|postgresql|postgres)://[^:@/\s]+:[^@\n]+@'),
        (
            re.compile(r'(?:user|admin|root|test):(?:password|pass|admin|root|test)@'),
            re.compile(r'(?:user|username):password@'),
            re.compile(r'user:pass@'),
            re.compile(r'(?:devuser:devpass|airflow:airflow|agenta:agenta_password)@'),
            re.compile(r'(?:example|placeholder|your[-_]?(?:password|user)|<password>|my(?:user|password|db)|db(?:user|pass)|secret123|password123)', re.IGNORECASE),
            re.compile(r'(?:\$\{.*\}|%.*%|(?:mongodb|mysql|postgresql|postgres)://[^:@]+:\$[A-Za-z_][A-Za-z0-9_]*@)'),
            re.compile(r'localhost'),
            re.compile(r'(?:grep|rg\s|egrep|fgrep|findstr)'),
            re.compile(r'(?:://\.\*:\.\*@\.\*|://\[\^:\]\+:\[\^@\]\+@\[\^/\]\+|```)'),
        ),
    ),
    (
        'SECRET-012',
        re.compile(r'hf_[A-Za-z0-9]{34}'),
        (
            re.compile(r'(?:hf_xxxx|hf_example|hf_your|hf_REPLACE)', re.IGNORECASE),
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-013',
        re.compile(r'SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-014',
        re.compile(r'pypi-[A-Za-z0-9_-]{100,}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-015',
        re.compile(r'https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{24}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
        ),
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
        (
            re.compile(r'["\'](?:your[_-]?|REPLACE|INSERT|CHANGE|EXAMPLE|TODO|FIXME|xxx|\.{3,}|<[^>]+>|\$\{)', re.IGNORECASE),
            re.compile(r'["\'](?:password|changeme|change_me|secret|none|null|empty|default|admin|test|dev|local)', re.IGNORECASE),
            re.compile(r'["\'](?:\$[A-Z_]|\$\(|%[A-Z_]|\{\{)', re.IGNORECASE),
            re.compile(r'(?:example|sample|demo|tutorial|documentation)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-017',
        re.compile(r'SK[a-f0-9]{32}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
            re.compile(r'SK(?:X{32}|0{32}|1{32})', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-018',
        re.compile(r'[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
            re.compile(r'eyJ', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-019',
        re.compile(
            r'(?:azure|cognitive|openai)[_-]?(?:api)?[_-]?key[\'"]?\s*[:=]\s*[\'"]?([a-f0-9]{32})[\'"]?',
            re.IGNORECASE
        ),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder|your[_-]?key)', re.IGNORECASE),
        ),
    ),
    (
        'SECRET-020',
        re.compile(r'do[op]_v1_[a-f0-9]{64}'),
        (
            re.compile(r'(?:example|sample|test|fake|dummy|placeholder)', re.IGNORECASE),
        ),
    ),
)


def _rule_findings(rule: Tuple[str, Pattern, Tuple[Pattern, ...]], content: str, lines: List[str],
                   line_offsets: List[int]) -> Iterator[Tuple[str, int]]:
    rule_id, pattern, exclude_patterns = rule

    @lru_cache(maxsize=None)
    def is_excluded(line_index: int) -> bool:
        return any(exclude.search(lines[line_index]) for exclude in exclude_patterns)

    for match in pattern.finditer(content):
        first_line = bisect_right(line_offsets, match.start()) - 1
        last_line = bisect_right(line_offsets, max(match.start(), match.end() - 1)) - 1
        if not any(is_excluded(line_index) for line_index in range(first_line, last_line + 1)):
            yield rule_id, first_line + 1


def find_secrets(content: str) -> List[Dict]:
    """Return one {rule_id, line} finding (1-based line) per rule and line with a secret match no exclusion drops."""
    lines = content.split('\n')
    line_offsets = list(accumulate((len(line) + 1 for line in lines[:-1]), initial=0))
    found = dict.fromkeys(
        finding for rule in SECRET_RULES for finding in _rule_findings(rule, content, lines, line_offsets))
    return [{"rule_id": rule_id, "line": line} for rule_id, line in found]
