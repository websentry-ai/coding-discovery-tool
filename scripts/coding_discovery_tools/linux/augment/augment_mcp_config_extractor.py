"""
MCP config extraction for Augment Code on Linux systems.

The parser and User-scope read are OS-agnostic and inherited from the macOS
extractor. Only the workspace walk differs on Linux (``/`` root + Linux system
dirs), so this subclass overrides the ``_workspace_search_roots`` /
``_should_skip_workspace_path`` seams.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ...linux_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.augment.augment_mcp_config_extractor import (
    MacOSAugmentMCPConfigExtractor,
)

logger = logging.getLogger(__name__)


class LinuxAugmentMCPConfigExtractor(MacOSAugmentMCPConfigExtractor):
    """Augment MCP extractor on Linux; overrides only the workspace walk."""

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (Linux)."""
        root_path = Path("/")
        try:
            return [(root_path, top_dir) for top_dir in get_top_level_directories(root_path)]
        except (PermissionError, OSError) as exc:
            logger.debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the Linux workspace walk (skip + Linux system dirs)."""
        return should_skip_path(item) or should_skip_system_path(item)
