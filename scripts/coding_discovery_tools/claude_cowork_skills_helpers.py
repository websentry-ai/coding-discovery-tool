"""
Shared helper functions for Claude Cowork skills extraction.

Cowork keeps SKILL.md files inside Claude Desktop's Application Support tree
under ``local-agent-mode-sessions/skills-plugin/<bundle-uuid>/...``. The same
skill often appears under several bundle UUIDs as Claude Desktop downloads
new versions, so we deduplicate by skill name and keep the newest copy.

Stdlib only — this module runs on customer machines and may not have third-
party packages installed.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# File I/O helpers (OS-agnostic; inlined to avoid a cross-platform import
# dependency on macos_extraction_helpers from Windows code).
# ──────────────────────────────────────────────────────────────────────────────

def _get_file_metadata(md_path: Path) -> Dict:
    """Return file size and last-modified timestamp (ISO-8601 UTC)."""
    stat = md_path.stat()
    return {
        "size": stat.st_size,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z",
    }


def _read_file_content(md_path: Path, file_size: int) -> Tuple[str, bool]:
    """Read file contents, truncating to MAX_CONFIG_FILE_SIZE if necessary."""
    if file_size > MAX_CONFIG_FILE_SIZE:
        logger.warning(
            f"Cowork skill file {md_path} exceeds size limit "
            f"({file_size} > {MAX_CONFIG_FILE_SIZE} bytes). Truncating."
        )
        try:
            with md_path.open("rb") as fh:
                chunk = fh.read(MAX_CONFIG_FILE_SIZE)
            return chunk.decode("utf-8", errors="replace"), True
        except Exception as e:
            logger.warning(f"Error reading truncated Cowork skill {md_path}: {e}")
            return "", True
    return md_path.read_text(encoding="utf-8", errors="replace"), False


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Top-level directory under Claude Desktop's Application Support that holds
# Cowork session state, including the on-disk skills plugin bundles.
COWORK_SESSIONS_DIR = "local-agent-mode-sessions"

# Subdirectory under sessions root where downloaded skill bundles live.
SKILLS_PLUGIN_DIR = "skills-plugin"

# The SKILL.md filename (matched case-insensitively).
SKILL_FILE_NAME_LOWER = "skill.md"

# Skill names that exist only at runtime and have no persistent on-disk
# representation we want to report on. If a SKILL.md happens to appear under
# one of these names we still skip it — they are noise, not user content.
RUNTIME_ONLY_NAMES = frozenset({"context"})

# Directories under the sessions root that represent ephemeral per-session
# scratch space. They appear and disappear unpredictably as sessions start
# and end, so we exclude any SKILL.md found beneath them.
EPHEMERAL_SESSION_PREFIX = "local_"

# Defensive: never report a skill whose path is under the user's ~/.claude/
# tree; that's Claude Code's territory and is reported by the Claude Code
# extractor under a different tool name.
CLAUDE_CODE_DIR_NAME = ".claude"


# ──────────────────────────────────────────────────────────────────────────────
# Frontmatter parsing
# ──────────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_skill_frontmatter(text: str) -> Dict[str, str]:
    """
    Parse the YAML frontmatter block at the top of a SKILL.md file.

    Only ``key: value`` pairs at the top level are extracted. Quoted values
    have their surrounding quotes stripped. Lines that don't match the
    pattern are ignored. Returns an empty dict if there is no frontmatter
    or it can't be parsed.

    Args:
        text: Full file contents (or the head of it).

    Returns:
        Dict mapping frontmatter keys to their string values.
    """
    if not text:
        return {}

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}

    block = match.group(1)
    result: Dict[str, str] = {}
    for line in block.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def extract_skill_name(md_path: Path, content: str, frontmatter: Dict[str, str]) -> str:
    """
    Resolve the canonical skill name.

    Priority:
        1. ``name`` field from frontmatter (if non-empty).
        2. First ``# H1`` heading in the markdown body.
        3. Parent directory name (the bundle's named folder).
    """
    name = (frontmatter.get("name") or "").strip()
    if name:
        return name

    if content:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                heading = stripped[2:].strip()
                if heading:
                    return heading

    return md_path.parent.name


# ──────────────────────────────────────────────────────────────────────────────
# Path filters
# ──────────────────────────────────────────────────────────────────────────────

def is_ephemeral_session_path(md_path: Path) -> bool:
    """
    Return True if any parent directory in the path is an ephemeral session
    directory (e.g. ``local_<uuid>/``). Skills under these directories are
    tied to a single Cowork session and get wiped when the session ends —
    we tag them with ``scope="session_ephemeral"`` to distinguish them from
    persistent user-level skills.
    """
    # Assumes local_ prefix only appears in session dirs directly under
    # local-agent-mode-sessions/, not in bundle names under skills-plugin/
    return any(
        part.startswith(EPHEMERAL_SESSION_PREFIX) for part in md_path.parts
    )


def resolve_cowork_scope(md_path: Path) -> str:
    """
    Determine the appropriate Cowork scope for a SKILL.md path.

    Cowork has two persistence tiers:
        * ``session_ephemeral`` — lives under ``local_<uuid>/`` and dies with
          the session. Typically created on the fly via ``/skill-creator``.
        * ``user`` — lives under ``skills-plugin/<bundle-uuid>/...`` and
          survives across sessions. Covers built-in, core, and personal
          skills that have been permanently installed.
    """
    return "session_ephemeral" if is_ephemeral_session_path(md_path) else "user"


def is_claude_code_path(md_path: Path) -> bool:
    """
    Return True if the resolved path lives under the user's home-level
    ``~/.claude/`` directory (Claude Code's territory). Used as a defensive
    cross-check so we never accidentally classify a Claude Code skill as a
    Cowork skill.

    Only the home-level ``.claude/`` is checked — ``.claude`` segments that
    appear deeper in the Cowork Application Support tree (e.g. inside
    ``local_<uuid>/.claude/skills/...``) are legitimate Cowork session paths
    and must NOT be excluded.
    """
    try:
        resolved = md_path.resolve()
        claude_code_root = Path.home() / CLAUDE_CODE_DIR_NAME
        return resolved == claude_code_root or claude_code_root in resolved.parents
    except (OSError, ValueError):
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Deduplication
# ──────────────────────────────────────────────────────────────────────────────

def deduplicate_skills(skills: List[Dict]) -> List[Dict]:
    """
    Collapse duplicate ``user``-scope skills down to one entry per name,
    keeping the most recent ``last_modified``. Cowork stores several
    versioned bundle UUIDs side-by-side under ``skills-plugin/``, so the
    same persistent skill typically appears 2-N times.

    ``session_ephemeral`` skills are passed through untouched: each lives
    under its own ``local_<uuid>/`` session directory and is a genuinely
    distinct installation that the backend keys on ``file_path``.

    Skills without a scope are treated like ``user`` scope for dedup
    purposes (keeps prior call-site behavior for tests and any legacy
    callers).
    """
    SESSION_EPHEMERAL = "session_ephemeral"
    by_name: Dict[str, Dict] = {}
    pass_through: List[Dict] = []
    for skill in skills:
        name = (skill.get("skill_name") or "").strip().lower()
        if not name:
            continue
        scope = (skill.get("scope") or "").strip().lower()
        if scope == SESSION_EPHEMERAL:
            pass_through.append(skill)
            continue
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = skill
            continue
        # Prefer the entry with the larger (more recent) last_modified
        # timestamp. Both values are ISO-8601 strings in UTC, so a string
        # comparison gives correct chronological ordering.
        if (skill.get("last_modified") or "") > (existing.get("last_modified") or ""):
            by_name[name] = skill
    return list(by_name.values()) + pass_through


# ──────────────────────────────────────────────────────────────────────────────
# Skill dict construction
# ──────────────────────────────────────────────────────────────────────────────

def build_cowork_skill_dict(md_path: Path) -> Optional[Dict]:
    """
    Build a single Cowork skill payload dict from a SKILL.md path.

    The returned shape conforms to the backend's ``ALLOWED_SKILL_FIELDS``
    contract so it routes through the existing ingestion code path
    unchanged. Scope is derived from the on-disk location:
        * ``user`` — under ``skills-plugin/...`` (persistent).
        * ``session_ephemeral`` — under ``local_<uuid>/...`` (lives for
          one Cowork session only).

    Returns:
        Skill dict, or ``None`` if the file should be skipped (unreadable,
        runtime-only name, etc.).
    """
    try:
        if not md_path.exists() or not md_path.is_file():
            return None

        metadata = _get_file_metadata(md_path)
        content, truncated = _read_file_content(md_path, metadata["size"])
        frontmatter = parse_skill_frontmatter(content) if content else {}
        skill_name = extract_skill_name(md_path, content, frontmatter).strip()

        if not skill_name:
            return None
        if skill_name.lower() in RUNTIME_ONLY_NAMES:
            return None

        return {
            "file_path": str(md_path),
            "file_name": md_path.name,
            "project_path": str(Path.home()),
            "content": content,
            "size": metadata["size"],
            "last_modified": metadata["last_modified"],
            "truncated": truncated,
            "scope": resolve_cowork_scope(md_path),
            "skill_name": skill_name,
            "type": "skill",
        }
    except PermissionError as e:
        logger.warning(f"Permission denied reading Cowork skill {md_path}: {e}")
        return None
    except UnicodeDecodeError as e:
        logger.warning(f"Unable to decode Cowork skill {md_path} as text: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading Cowork skill {md_path}: {e}")
        return None


# Re-export so callers don't have to reach into constants.
__all__ = [
    "COWORK_SESSIONS_DIR",
    "SKILLS_PLUGIN_DIR",
    "SKILL_FILE_NAME_LOWER",
    "RUNTIME_ONLY_NAMES",
    "EPHEMERAL_SESSION_PREFIX",
    "CLAUDE_CODE_DIR_NAME",
    "MAX_CONFIG_FILE_SIZE",
    "parse_skill_frontmatter",
    "extract_skill_name",
    "is_ephemeral_session_path",
    "is_claude_code_path",
    "resolve_cowork_scope",
    "deduplicate_skills",
    "build_cowork_skill_dict",
]
