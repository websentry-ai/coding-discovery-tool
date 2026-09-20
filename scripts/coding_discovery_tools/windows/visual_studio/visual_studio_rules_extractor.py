r"""
Custom-instruction and custom-agent extraction for GitHub Copilot in Visual Studio.

Two user-profile files, both documented as Visual Studio's own defaults:

    %USERPROFILE%\copilot-instructions.md        user-level preferences
      (learn.microsoft.com/visualstudio/ide/copilot-chat-context)
    %USERPROFILE%\.github\agents\*.agent.md      user-level custom agents
      (learn.microsoft.com/visualstudio/ide/copilot-specialized-agents)

Repo-scoped Copilot files are deliberately NOT read here. ``.github/copilot-instructions.md``,
``.github/instructions/``, ``.github/prompts/`` and repo ``.github/agents/`` are read by
GitHub Copilot on every surface, so they already belong to the VS Code and CLI rows;
claiming them for Visual Studio would duplicate them, not discover them.

An agent file's ``tools:`` frontmatter grants capabilities such as ``editfiles`` and
``runcommandinterminal``, so it is a permission surface, not just a prompt.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...constants import is_symlink_or_junction
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    scan_windows_user_directories,
)

logger = logging.getLogger(__name__)

USER_INSTRUCTIONS_FILENAME = "copilot-instructions.md"
USER_AGENTS_DIR = Path(".github") / "agents"
AGENT_FILE_GLOB = "*.agent.md"


class WindowsVisualStudioRulesExtractor:
    """Extractor for Visual Studio Copilot user-level rules and agents on Windows."""

    def extract_all_visual_studio_rules(self) -> List[Dict]:
        """Project-shaped rule list, one entry per user profile that has any."""
        projects_by_root: Dict[str, List[Dict]] = {}

        def extract_for_user(user_home: Path) -> None:
            for rule_file in self._rule_files(user_home):
                rule_info = extract_single_rule_file(
                    rule_file,
                    find_project_root_func=lambda _f, home=user_home: home,
                    scope="user",
                )
                if rule_info:
                    add_rule_to_project(rule_info, str(user_home), projects_by_root)
                    logger.debug(f"Found Visual Studio Copilot user rule: {rule_file}")

        try:
            scan_windows_user_directories(extract_for_user)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error scanning user directories for Visual Studio rules: {e}")

        return build_project_list(projects_by_root)

    def _rule_files(self, user_home: Path) -> List[Path]:
        """The user-profile instruction and agent files that exist. Never raises."""
        files: List[Path] = []
        instructions = user_home / USER_INSTRUCTIONS_FILENAME
        if self._readable_within_profile(instructions, user_home):
            files.append(instructions)

        agents_dir = user_home / USER_AGENTS_DIR
        if self._readable_within_profile(agents_dir, user_home, want_dir=True):
            try:
                files.extend(
                    f for f in agents_dir.glob(AGENT_FILE_GLOB)
                    if self._readable_within_profile(f, user_home)
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not list {agents_dir}: {e}")

        return files

    @staticmethod
    def _readable_within_profile(path: Path, user_home: Path, want_dir: bool = False) -> bool:
        r"""Whether ``path`` is a real file/dir whose target stays inside ``user_home``.

        These paths are inside a profile the profile's owner controls, but an
        all-user scan reads them as Administrator or LOCAL SYSTEM. A junction at
        ``copilot-instructions.md`` or ``.github\agents`` would otherwise be followed
        by ``is_file()``/``is_dir()`` and up to 50 KB of a file only the elevated
        scanner can read would be copied into the uploaded rule content.

        Both halves are needed: the reparse-point check rejects the obvious case
        cheaply, and the resolved-path containment check catches a symlink anywhere
        in the chain, including one on an intermediate directory. Never raises.
        """
        try:
            if is_symlink_or_junction(path):
                logger.debug(f"Skipping reparse point under {user_home}: {path}")
                return False
            if not (path.is_dir() if want_dir else path.is_file()):
                return False
            path.resolve(strict=True).relative_to(user_home.resolve(strict=True))
            return True
        except ValueError:
            logger.debug(f"Skipping rule path resolving outside {user_home}: {path}")
            return False
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not resolve {path}: {e}")
            return False
