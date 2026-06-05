"""GitHub Copilot detection for Linux."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseCopilotDetector as BaseCopilotDetectorBase
from ...linux.jetbrains.jetbrains import LinuxJetBrainsDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


def _load_extension_json(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


# Recent VS Code ships GitHub Copilot / Copilot Chat as BUILT-IN extensions
# inside the install tree, so they never appear in the per-user
# ``~/.vscode/extensions/extensions.json``. These are the common Linux install
# roots (deb/rpm, /opt, snap) for stable + Insiders, and the bundled folder name.
_VSCODE_APP_EXTENSION_ROOTS = [
    Path("/usr/share/code/resources/app/extensions"),
    Path("/usr/share/code-insiders/resources/app/extensions"),
    Path("/usr/lib/code/extensions"),
    Path("/opt/visual-studio-code/resources/app/extensions"),
    Path("/opt/visual-studio-code-insiders/resources/app/extensions"),
    Path("/snap/code/current/usr/share/code/resources/app/extensions"),
]
_VSCODE_BUILTIN_COPILOT_DIRS = ("copilot", "copilot-chat")
# Per-user VS Code data dirs — presence means the user actually uses VS Code, so
# a system-wide built-in Copilot can be attributed to them (not every user).
_VSCODE_USER_DATA_DIRS = [
    Path(".config/Code/User"),
    Path(".config/Code - Insiders/User"),
]


def _read_extension_version(ext_dir: Path) -> str:
    """Read ``version`` from a VS Code extension's package.json (best-effort)."""
    try:
        data = json.loads((ext_dir / "package.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("version", "unknown")
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return "unknown"


class LinuxCopilotDetector(BaseCopilotDetectorBase):
    """Detects GitHub Copilot across VS Code and all JetBrains IDEs on Linux."""

    @property
    def tool_name(self) -> str:
        return "GitHub Copilot"

    def detect_copilot(self) -> List[Dict]:
        all_results = []
        all_results.extend(self._detect_vscode_all_users())
        all_results.extend(self._detect_jetbrains_all_users())
        return all_results

    def _detect_vscode_all_users(self) -> List[Dict]:
        results = []
        for user_home in get_linux_user_homes():
            try:
                results.extend(self._detect_vscode_for_user(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_home}: {e}")
        return results

    def _detect_vscode_for_user(self, user_home: Path) -> List[Dict]:
        results = []
        vscode_ext_path = user_home / ".vscode" / "extensions" / "extensions.json"
        extensions_data = _load_extension_json(vscode_ext_path)

        for ext in extensions_data:
            ext_id = ext.get("identifier", {}).get("id", "").lower()
            if ext_id == "github.copilot":
                results.append({
                    "name": "GitHub Copilot (VS Code)",
                    "version": ext.get("version", "unknown"),
                    "publisher": "GitHub",
                    "install_path": str(vscode_ext_path.parent),
                })
            elif ext_id == "github.copilot-chat":
                results.append({
                    "name": "GitHub Copilot Chat (VS Code)",
                    "version": ext.get("version", "unknown"),
                    "publisher": "GitHub",
                    "install_path": str(vscode_ext_path.parent),
                })

        # Fall back to BUILT-IN Copilot (bundled in the VS Code install) when no
        # marketplace Copilot extension is present, so built-in users — and their
        # VS Code MCP servers (~/.config/Code/User/mcp.json) — aren't missed.
        if not results:
            results.extend(self._detect_vscode_builtin_copilot(user_home))

        return results

    def _detect_vscode_builtin_copilot(self, user_home: Path) -> List[Dict]:
        """Detect Copilot shipped built-in with the VS Code install on Linux.

        Reported only when this user actually uses VS Code (has a ``Code/User``
        data dir). Returns at most one entry by design — a single detection is
        enough to trigger downstream rules/MCP extraction, and built-in Copilot
        bundles chat inside the same ``copilot`` extension, so a second row would
        only duplicate the same MCP servers.
        """
        uses_vscode = False
        for rel in _VSCODE_USER_DATA_DIRS:
            try:
                if (user_home / rel).exists():
                    uses_vscode = True
                    break
            except OSError:
                continue
        if not uses_vscode:
            logger.debug(f"No VS Code user data dir under {user_home}; skipping built-in Copilot")
            return []

        for ext_root in _VSCODE_APP_EXTENSION_ROOTS:
            for dir_name in _VSCODE_BUILTIN_COPILOT_DIRS:
                copilot_dir = ext_root / dir_name
                try:
                    if not copilot_dir.is_dir():
                        continue
                except OSError:
                    continue
                version = _read_extension_version(copilot_dir)
                logger.debug(f"Detected built-in VS Code Copilot {version} at {copilot_dir}")
                return [{
                    "name": "GitHub Copilot (VS Code)",
                    "version": version,
                    "publisher": "GitHub",
                    "install_path": str(copilot_dir),
                }]
        logger.debug(f"VS Code in use under {user_home} but no built-in Copilot extension found")
        return []

    def _detect_jetbrains_all_users(self) -> List[Dict]:
        results = []
        all_ides = LinuxJetBrainsDetector().detect() or []
        for ide in all_ides:
            for plugin_name in ide.get("plugins", []):
                if "copilot" in plugin_name.lower():
                    results.append({
                        "name": f"GitHub Copilot ({ide['name']})",
                        "version": ide.get("version", "unknown"),
                        "publisher": "GitHub",
                        "ide": ide["name"],
                        "install_path": ide.get("_config_path") or ide.get("install_path"),
                    })
        return results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        return self.detect_copilot()
