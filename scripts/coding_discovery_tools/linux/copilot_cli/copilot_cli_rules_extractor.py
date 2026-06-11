"""
GitHub Copilot CLI rules/instructions extraction for Linux systems.

The source set and the depth-bounded project walk are OS-agnostic and inherited
from ``MacOSCopilotCliRulesExtractor`` (DRY). This subclass overrides only the
OS-specific seams via ``linux_extraction_helpers``.
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
        """Skip project/system dirs AND other-tool config dirs (``~/.<tool>``) so the
        walk doesn't mis-attribute another tool's bundled instructions to Copilot
        CLI. Uses the Linux ``should_skip_system_path`` (which does NOT skip
        ``/home``)."""
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )
