"""
GitHub Copilot CLI rules/instructions extraction for Linux systems.

DRY decision (CLAUDE.md): the 6-source detection logic (G1/G2 global, E1 env,
P1/P2/P3 project) and the depth-bounded project walk are OS-agnostic and live in
``MacOSCopilotCliRulesExtractor``. Only five OS primitives differ — the privilege
check, the all-users scan, the filesystem root, top-level enumeration, and the
system-dir skip predicate — so this subclass overrides exactly those seams (via
``linux_extraction_helpers``) and inherits the walk unchanged. Mirrors the
Windows subclass and the sibling Linux rules extractors (Codex / Gemini).
"""

from pathlib import Path
from typing import List

from ...constants import traverses_other_tool_config_dir
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    get_top_level_directories,
    is_running_as_root,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.copilot_cli_rules_extractor import (
    MacOSCopilotCliRulesExtractor,
)


class LinuxCopilotCliRulesExtractor(MacOSCopilotCliRulesExtractor):
    """GitHub Copilot CLI rules extractor on Linux.

    Overrides only the OS-specific seams; the source set and walk are inherited
    from ``MacOSCopilotCliRulesExtractor``.
    """

    def _is_privileged(self) -> bool:
        """True when running as root (gates the per-user E1 env scan)."""
        return is_running_as_root()

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user`` for every Linux user home.

        ``get_linux_user_homes`` returns all human users when running as root
        (including ``/root``), else just the current user's home.
        """
        for user_home in get_linux_user_homes():
            extract_for_user(Path(user_home))

    def _filesystem_root(self) -> Path:
        """Root the project walk starts from (POSIX ``/`` on Linux)."""
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under the filesystem root, Linux system dirs excluded."""
        return list(get_top_level_directories(root_path))

    def _should_skip(self, item: Path) -> bool:
        """Skip project/system dirs AND other-tool config dirs (``~/.<tool>``) so
        the walk never descends into another tool's installed-extension packages
        and mis-attributes their bundled instructions to Copilot CLI. Uses the
        Linux ``should_skip_system_path`` (which does NOT skip ``/home``)."""
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )
