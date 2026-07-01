"""
GitHub Copilot CLI rules/instructions extraction for Windows systems.

DRY decision (CLAUDE.md): the 6-source detection logic (G1/G2 global, E1 env,
P1/P2/P3 project) and the depth-bounded project walk are OS-agnostic and live in
``MacOSCopilotCliRulesExtractor``. Only five OS primitives differ — the privilege
check, the all-users scan, the filesystem root, top-level enumeration, and the
system-dir skip predicate — so this subclass overrides exactly those seams
(via ``windows_extraction_helpers``) and inherits the ~250-line walk unchanged.
This mirrors how the sibling Windows rules extractors (Codex / Gemini / Claude)
special-case the same primitives without duplicating the walk.
"""

from pathlib import Path
from typing import List

from ...constants import traverses_other_tool_config_dir
from ...macos.copilot_cli.copilot_cli_rules_extractor import (
    MacOSCopilotCliRulesExtractor,
)
from ...windows_extraction_helpers import (
    get_windows_system_directories,
    is_running_as_admin,
    scan_windows_user_directories,
    should_skip_path,
)


class WindowsCopilotCliRulesExtractor(MacOSCopilotCliRulesExtractor):
    """GitHub Copilot CLI rules extractor on Windows.

    Overrides only the OS-specific seams; the source set and walk are inherited
    from ``MacOSCopilotCliRulesExtractor``.
    """

    def _is_privileged(self) -> bool:
        """True when running as administrator (gates the per-user E1 env scan)."""
        return is_running_as_admin()

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Scan every ``C:\\Users`` home when admin, else the current user only.

        ``scan_windows_user_directories`` already gates on admin internally and
        excludes the public/default pseudo-users.
        """
        scan_windows_user_directories(extract_for_user)

    def _filesystem_root(self) -> Path:
        """Drive anchor of the current user's home (e.g. ``C:\\``)."""
        return Path(Path.home().anchor)

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under the drive root, Windows system dirs excluded."""
        system_dirs = get_windows_system_directories()
        try:
            return [
                item
                for item in root_path.iterdir()
                if item.is_dir() and not should_skip_path(item, system_dirs)
            ]
        except (PermissionError, OSError):
            return []

    def _should_skip(self, item: Path) -> bool:
        """Skip project dirs (node_modules/.git/…), Windows system dirs, AND
        other-tool config dirs (``~/.<tool>``) so the walk never descends into
        another tool's installed-extension packages (e.g.
        ``.../.antigravity/extensions/<pkg>/.github``)."""
        return (
            should_skip_path(item, get_windows_system_directories())
            or traverses_other_tool_config_dir(item)
        )
