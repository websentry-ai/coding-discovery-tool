"""
Constants used across the AI tools discovery system
"""

import os
import stat

# Reparse tags that REDIRECT to another location. Windows-only concept; the
# ``stat`` constants are absent on POSIX (and on some builds), so fall back to
# the documented literals. Cloud placeholders (e.g. OneDrive) are reparse points
# too but are NOT redirects — they must stay traversable or a OneDrive-redirected
# Documents folder would silently stop being scanned.
_IO_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
_IO_REPARSE_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
_REDIRECTING_REPARSE_TAGS = frozenset({_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK})


def is_symlink_or_junction(path) -> bool:
    """True for POSIX symlinks and Windows symlinks *and directory junctions*.

    ``Path.is_symlink()`` returns False for an NTFS directory junction
    (``IO_REPARSE_TAG_MOUNT_POINT``) — which any user can create with
    ``mklink /J``, no admin required — and ``Path.is_junction()`` only exists on
    Python 3.12+ (this project supports 3.9+). So the reparse tag is inspected
    directly via ``lstat``. Without this, a planted junction bypasses the symlink
    guards and can redirect a privileged/all-user scan into another user's tree.

    Uses a SINGLE ``lstat`` (not ``is_symlink()`` + ``lstat``) because this runs
    once per directory entry of a whole-disk walk — the extra syscall is not free.

    Conservative: any ``lstat`` failure returns True (do not traverse). Never raises.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True

    # POSIX symlinks (and Windows symlinks, which lstat reports as S_IFLNK).
    if stat.S_ISLNK(st.st_mode):
        return True

    # ``st_reparse_tag`` exists only on Windows; 0 elsewhere -> not a redirect.
    # Junctions come back as S_IFDIR with a MOUNT_POINT tag, so S_ISLNK misses them.
    tag = getattr(st, "st_reparse_tag", 0)
    return bool(tag) and tag in _REDIRECTING_REPARSE_TAGS


# Invalid serial number values that should be ignored
INVALID_SERIAL_VALUES = [
    "TO BE FILLED BY O.E.M.",
    "DEFAULT STRING",
    "SERIALNUMBER",
    "SYSTEM SERIAL NUMBER",
    "NOT APPLICABLE",
    "N/A",
    "NONE",
    "NOT SPECIFIED",
    "OEM",
    "O.E.M.",
    "DEFAULT",
    "SYSTEM MANUFACTURER",
    "CHASSIS SERIAL NUMBER",
    "0",
    "00000000",
    "000000000000",
    "123456789",
    "XXXXXXXXXXXXXX",
    ""
]

# Command execution timeouts
COMMAND_TIMEOUT = 30
VERSION_TIMEOUT = 30
AUTH_STATUS_TIMEOUT = 15
KEYCHAIN_TIMEOUT = 5
KEYCHAIN_SERVICE_NAME = "Claude Code-credentials"

# Cursor rules extraction settings
MAX_CONFIG_FILE_SIZE = 50 * 1024  # 50KB in bytes
MAX_SEARCH_DEPTH = 10  # Maximum directory depth to search recursively
SKIP_DIRS = frozenset[str]({
    '.git', 'node_modules', 'venv', '__pycache__', '.venv', 'vendor', '.idea', '.vscode', 'Library', '.Trash', '.cache', 
    'Photos', 'Music', 'Movies', 'Pictures', 'Videos'
})
# System directories to skip when searching from root (macOS/Unix)
SKIP_SYSTEM_DIRS = {
    '/System', '/Library', '/private', '/usr', '/bin', '/sbin', '/opt',
    '/var', '/etc', '/tmp', '/cores', '/dev', '/home', '/net', '/Volumes',
    '/.fseventsd', '/.Spotlight-V100', '/.Trashes', '/.vol'
}

# Per-user AI-tool config directories (``~/.<tool>``). A project-rules/skills
# walk must not descend into a DIFFERENT tool's config dir: its contents —
# including installed extension/plugin packages like
# ``~/.antigravity/extensions/<pkg>/.github`` or
# ``~/.codex/.tmp/plugins/<pkg>/.agents/skills`` — belong to that tool, not to
# the scanned user's repositories. Each SKILL.md-reading tool still collects its
# OWN dir because the skills walk passes ``allow = SHARED_SKILL_DIRS | <own
# parent dirs>`` (so e.g. Kilo keeps ``.kilo``, OpenCode keeps ``.opencode``).
# Must list EVERY tool's per-user config dir — an omission lets other tools'
# walks over-collect bundled ``.agents``/``.claude`` skills from it. (Kept
# separate from the scope-classification set in
# ``macos_extraction_helpers._detect_rule_scope`` so scope rules don't change.)
OTHER_TOOL_CONFIG_DIRS = frozenset({
    ".cursor", ".claude", ".windsurf", ".devin", ".antigravity", ".roo", ".cline",
    ".clinerules", ".kilocode", ".kilo", ".gemini", ".codeium", ".junie",
    ".codex", ".opencode", ".copilot",
    # NOTE: ``.augment`` is intentionally omitted. The Augment extractor consults
    # this set but passes ``allow=SHARED_SKILL_DIRS`` (not its own ``.augment``),
    # so adding it here would make Augment skip its OWN dir. Add ``.augment`` only
    # once the Augment extractor allows its own parent dir (as the newer per-tool
    # extractors do).
})

# Shared cross-tool skill dirs the Copilot CLI skills walk legitimately collects
# from a repository root (the open Agent Skills convention). Exempted from the
# other-tool-config-dir skip in the SKILLS walk only — rules never read these.
SHARED_SKILL_DIRS = frozenset({".claude", ".agents"})


def traverses_other_tool_config_dir(path, allow=frozenset()):
    """True if any component of ``path`` is another tool's config dir.

    Stops a project walk from descending into a different AI tool's per-user
    config dir (``~/.<tool>``) — e.g. ``~/.antigravity/extensions/<pkg>`` —
    whose bundled ``.github``/``.claude`` files are that tool's, not the scanned
    user's projects. ``allow`` names dirs to NOT skip: the skills walk passes
    ``SHARED_SKILL_DIRS`` (``.claude``/``.agents``) it must still collect from a
    real repo root. Operates on ``path.parts`` so it is OS-agnostic.
    """
    skip = OTHER_TOOL_CONFIG_DIRS - allow
    return any(part in skip for part in path.parts)

# Cursor plan detection
CURSOR_DB_TIMEOUT = 5  # seconds
CURSOR_PLAN_KEY = "cursorAuth/stripeMembershipType"

# User filtering constants
MACOS_MIN_HUMAN_UID = 500
MACOS_SKIP_USER_DIRS = frozenset({"Shared"})
NON_INTERACTIVE_SHELLS = frozenset({
    "/usr/bin/false", "/usr/sbin/nologin", "/dev/null",
    "/bin/false", "/sbin/nologin",
})
DSCL_TIMEOUT = 5
WINDOWS_SKIP_USER_DIRS = frozenset({
    "Public", "Default", "Default User", "All Users", "TEMP",
})

