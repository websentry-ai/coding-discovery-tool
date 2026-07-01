"""
Constants used across the AI tools discovery system
"""

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
    'Photos', 'Music', 'Movies', 'Pictures', 'Public', 'Templates', 'Videos'
})

# System directories to skip when searching from root (macOS/Unix)
SKIP_SYSTEM_DIRS = {
    '/System', '/Library', '/private', '/usr', '/bin', '/sbin', '/opt',
    '/var', '/etc', '/tmp', '/cores', '/dev', '/home', '/net', '/Volumes',
    '/.fseventsd', '/.Spotlight-V100', '/.Trashes', '/.vol'
}

# Per-user AI-tool config directories (``~/.<tool>``). A project-rules/skills
# walk must not descend into a DIFFERENT tool's config dir: its contents —
# including installed extension packages like
# ``~/.antigravity/extensions/<pkg>/.github`` — belong to that tool, not to the
# scanned user's repositories. (Kept separate from the scope-classification set
# in ``macos_extraction_helpers._detect_rule_scope`` so scope rules don't change.)
OTHER_TOOL_CONFIG_DIRS = frozenset({
    ".cursor", ".claude", ".windsurf", ".antigravity", ".roo", ".cline",
    ".clinerules", ".kilocode", ".gemini", ".codeium", ".junie",
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

