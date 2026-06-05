import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseCopilotDetector as BaseCopilotDetectorBase
from ...macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)


def _load_extension_json(path: Path) -> List[Dict]:
    """Helper function to parse the VS Code extensions file."""
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


# Recent VS Code ships GitHub Copilot / Copilot Chat as BUILT-IN extensions
# inside the application bundle, so they never appear in the per-user
# ``~/.vscode/extensions/extensions.json`` the marketplace path reads. These are
# the macOS app-bundle extension roots (stable + Insiders) and the bundled
# Copilot folder name.
_VSCODE_APP_EXTENSION_ROOTS = [
    Path("/Applications/Visual Studio Code.app/Contents/Resources/app/extensions"),
    Path("/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/extensions"),
]
_VSCODE_BUILTIN_COPILOT_DIRS = ("copilot", "copilot-chat")

# Per-user VS Code data dirs. Their presence means the user actually uses VS Code,
# so a machine-wide built-in Copilot can be attributed to them (and not to every
# unrelated user during a root scan).
_VSCODE_USER_DATA_DIRS = [
    Path("Library/Application Support/Code/User"),
    Path("Library/Application Support/Code - Insiders/User"),
]


def _read_builtin_copilot_identity(ext_dir: Path):
    """Return ``(tool_name, version)`` for a bundled copilot extension from its
    package.json.

    Recent VS Code consolidates everything into the ``copilot`` folder, which is
    actually the **Copilot Chat** extension (``name: "copilot-chat"``,
    ``displayName: "GitHub Copilot Chat"``) — the extension that consumes
    ``mcp.json``. Label it ``GitHub Copilot Chat (VS Code)`` accordingly (and to
    match the marketplace ``github.copilot-chat`` mapping); a plain ``copilot``
    extension stays ``GitHub Copilot (VS Code)``. Best-effort.
    """
    name_label, version = "GitHub Copilot (VS Code)", "unknown"
    try:
        data = json.loads((ext_dir / "package.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            version = data.get("version", "unknown")
            ext_name = str(data.get("name") or "").lower()
            display = str(data.get("displayName") or "").lower()
            if "copilot-chat" in ext_name or "copilot chat" in display:
                name_label = "GitHub Copilot Chat (VS Code)"
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return name_label, version


class MacOSCopilotDetector(BaseCopilotDetectorBase):
    """
    Detects GitHub Copilot across VS Code and all JetBrains IDEs on macOS.
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "GitHub Copilot"

    def detect_copilot(self) -> List[Dict]:
        """
        Returns ALL detected Copilot instances with their install paths.
        When running as root, scans all users in /Users/.
        """
        all_results = []

        # Add VS Code detections
        all_results.extend(self._detect_vscode_all_users())

        # Add JetBrains detections
        all_results.extend(self._detect_jetbrains_all_users())

        return all_results

    def _detect_vscode_all_users(self) -> List[Dict]:
        """
        Detect VS Code Copilot for all users when running as root.
        For regular users, only checks their own directory.
        """
        results = []

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            vscode_results = self._detect_vscode_for_user(user_dir)
                            results.extend(vscode_results)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            vscode_results = self._detect_vscode_for_user(Path.home())
            results.extend(vscode_results)

        return results

    def _detect_vscode_for_user(self, user_home: Path) -> List[Dict]:
        """
        Detect VS Code Copilot for a specific user.
        """
        results = []
        vscode_ext_path = user_home / '.vscode' / 'extensions' / 'extensions.json'

        extensions_data = _load_extension_json(vscode_ext_path)

        for ext in extensions_data:
            ext_id = ext.get('identifier', {}).get('id', '').lower()

            if ext_id == "github.copilot":
                results.append({
                    "name": "GitHub Copilot (VS Code)",
                    "version": ext.get('version', 'unknown'),
                    "publisher": "GitHub",
                    "install_path": str(vscode_ext_path.parent)
                })
            elif ext_id == "github.copilot-chat":
                results.append({
                    "name": "GitHub Copilot Chat (VS Code)",
                    "version": ext.get('version', 'unknown'),
                    "publisher": "GitHub",
                    "install_path": str(vscode_ext_path.parent)
                })

        # Fall back to BUILT-IN Copilot (bundled in the VS Code app) when no
        # marketplace Copilot extension is installed. Without this, users on the
        # built-in Copilot are never detected and their VS Code MCP servers
        # (``Code/User/mcp.json``) are silently skipped.
        if not results:
            results.extend(self._detect_vscode_builtin_copilot(user_home))

        return results

    def _detect_vscode_builtin_copilot(self, user_home: Path) -> List[Dict]:
        """Detect Copilot shipped built-in with the VS Code application.

        Only reported when this user actually uses VS Code (has a ``Code/User``
        data dir), so a machine-wide app install isn't attributed to unrelated
        users during a root all-users scan.

        Returns at most ONE entry by design: a single "GitHub Copilot (VS Code)"
        detection is all that's needed to trigger downstream rules/MCP extraction
        (which scan the user's VS Code data dir once). Built-in Copilot ships its
        chat surface inside the same ``copilot`` extension, so emitting a second
        ``copilot-chat`` row would only double-process and duplicate the same MCP
        servers — unlike the marketplace path, where ``github.copilot`` and
        ``github.copilot-chat`` are genuinely separate installs.
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
            logger.debug("No VS Code user data dir under %s; skipping built-in Copilot", user_home)
            return []

        for ext_root in _VSCODE_APP_EXTENSION_ROOTS:
            for dir_name in _VSCODE_BUILTIN_COPILOT_DIRS:
                copilot_dir = ext_root / dir_name
                try:
                    if not copilot_dir.is_dir():
                        continue
                except OSError:
                    continue
                name_label, version = _read_builtin_copilot_identity(copilot_dir)
                logger.debug("Detected built-in VS Code %s %s at %s", name_label, version, copilot_dir)
                return [{
                    "name": name_label,
                    "version": version,
                    "publisher": "GitHub",
                    "install_path": str(copilot_dir),
                }]
        logger.debug("VS Code in use under %s but no built-in Copilot extension found", user_home)
        return []

    def _detect_jetbrains_all_users(self) -> List[Dict]:
        """
        Detect JetBrains Copilot for all users when running as root.
        """
        detected_results = []

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            jetbrains_results = self._detect_jetbrains_for_user(user_dir)
                            detected_results.extend(jetbrains_results)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            jetbrains_results = self._detect_jetbrains_for_user(Path.home())
            detected_results.extend(jetbrains_results)

        return detected_results

    def _detect_jetbrains_for_user(self, user_home: Path) -> List[Dict]:
        """
        Detect JetBrains Copilot for a specific user.
        """
        detected_results = []

        jetbrains_detector = MacOSJetBrainsDetector()
        jetbrains_detector.user_home = user_home

        all_ides = jetbrains_detector.detect() or []

        for ide in all_ides:
            plugins = ide.get("plugins", [])

            for plugin_name in plugins:
                if "copilot" in plugin_name.lower():
                    detected_results.append({
                        "name": f"GitHub Copilot ({ide['name']})",
                        "version": ide.get("version", "unknown"),
                        "publisher": "GitHub",
                        "ide": ide['name'],
                        "install_path": ide.get("config_path") or ide.get("install_path")
                    })

        return detected_results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """Entry point used by the AIToolsDetector factory."""
        return self.detect_copilot()
