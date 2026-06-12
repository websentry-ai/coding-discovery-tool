import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseCopilotDetector
from ...vscode_extension_helpers import find_extension_in_editor
from ...windows_extraction_helpers import is_running_as_admin
from ..jetbrains.jetbrains import WindowsJetBrainsDetector

logger = logging.getLogger(__name__)


def _load_jsonc(file_path: Path) -> Optional[Dict]:
    """
    Load a JSON file that may contain comments (JSONC).
    """
    import re

    if not file_path.exists():
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = f.read()

        pattern = r'("(?:\\.|[^"\\])*")|(/\*.*?\*/)|(//.*$)'

        def replace(match):
            if match.group(1):
                return match.group(1)
            return ""

        clean = re.sub(pattern, replace, raw, flags=re.DOTALL | re.MULTILINE)
        return json.loads(clean)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Error loading JSONC file {file_path}: {e}")
        return None


# Recent VS Code ships GitHub Copilot / Copilot Chat as BUILT-IN extensions in
# the install tree, so they never appear in the per-user .vscode\extensions
# folder the marketplace glob scans. The bundled folder name + system-wide
# install roots (per-user user-install roots are derived from user_home).
_VSCODE_BUILTIN_COPILOT_DIRS = ("copilot", "copilot-chat")
_VSCODE_SYSTEM_APP_EXTENSION_ROOTS = [
    Path(r"C:\Program Files\Microsoft VS Code\resources\app\extensions"),
    Path(r"C:\Program Files\Microsoft VS Code Insiders\resources\app\extensions"),
    Path(r"C:\Program Files (x86)\Microsoft VS Code\resources\app\extensions"),
]
# Per-user VS Code data dirs (relative to user_home) — presence means the user
# actually uses VS Code, so a system-wide built-in install can be attributed to
# them and not to every user during an admin all-users scan.
_VSCODE_USER_DATA_DIRS = [
    Path("AppData/Roaming/Code/User"),
    Path("AppData/Roaming/Code - Insiders/User"),
]


class WindowsGitHubCopilotDetector(BaseCopilotDetector):
    """
    Detects GitHub Copilot across VS Code and all JetBrains IDEs on Windows.
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "GitHub Copilot"

    def detect_copilot(self) -> List[Dict]:
        """
        Returns ALL detected Copilot instances with their install paths.
        When running as administrator, scans all users in C:\\Users.
        """
        all_results = []

        # Add VS Code detections
        all_results.extend(self._detect_vscode_all_users())

        # Add JetBrains detections
        all_results.extend(self._detect_jetbrains_all_users())

        return all_results

    def _detect_vscode_all_users(self) -> List[Dict]:
        """
        Detect VS Code Copilot for all users when running as administrator.
        For regular users, only checks their own directory.
        """
        results = []

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
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

        Reads the LIVE ``.vscode\\extensions\\extensions.json`` registry rather
        than globbing for ``github.copilot*`` folders. VS Code rewrites this
        registry on uninstall, but the extension FOLDER survives
        (microsoft/vscode#81046), so the old folder glob produced phantom rows
        for uninstalled Copilot. This matches the SAFE macOS/Linux path.
        """
        results = []
        vscode_ext_dir = user_home / ".vscode" / "extensions"

        for ext_id, name in (
            ("github.copilot", "GitHub Copilot (VS Code)"),
            ("github.copilot-chat", "GitHub Copilot Chat (VS Code)"),
        ):
            entry = find_extension_in_editor(user_home, "Code", ext_id)
            if entry is None:
                continue
            _location, version = entry
            results.append({
                "name": name,
                "version": version or "unknown",
                "publisher": "GitHub",
                "install_path": str(vscode_ext_dir),
            })
            logger.info(f"Detected: {name} v{version or 'unknown'} at {vscode_ext_dir}")

        # Fall back to BUILT-IN Copilot (bundled in the VS Code install) when no
        # marketplace Copilot extension is present, so built-in users — and their
        # VS Code MCP servers (%APPDATA%\Code\User\mcp.json) — aren't missed.
        if not results:
            results.extend(self._detect_vscode_builtin_copilot(user_home))

        return results

    def _vscode_app_extension_roots(self, user_home: Path) -> List[Path]:
        """VS Code install extension roots to probe for built-in Copilot: the
        per-user user-install location (under the user's LocalAppData) plus the
        system-wide install locations."""
        roots = []
        local_programs = user_home / "AppData" / "Local" / "Programs"
        for app in ("Microsoft VS Code", "Microsoft VS Code Insiders"):
            roots.append(local_programs / app / "resources" / "app" / "extensions")
        roots.extend(_VSCODE_SYSTEM_APP_EXTENSION_ROOTS)
        return roots

    def _detect_vscode_builtin_copilot(self, user_home: Path) -> List[Dict]:
        """Detect Copilot shipped built-in with the VS Code install on Windows.

        Reported only when this user actually uses VS Code (has a ``Code\\User``
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

        for ext_root in self._vscode_app_extension_roots(user_home):
            for dir_name in _VSCODE_BUILTIN_COPILOT_DIRS:
                copilot_dir = ext_root / dir_name
                try:
                    if not copilot_dir.is_dir():
                        continue
                except OSError:
                    continue
                # The consolidated built-in "copilot" folder is actually the
                # Copilot Chat extension (name="copilot-chat") — the MCP consumer
                # — so label it accordingly (matches the marketplace
                # github.copilot-chat mapping); a plain "copilot" stays generic.
                version, name_label = "unknown", "GitHub Copilot (VS Code)"
                data = _load_jsonc(copilot_dir / "package.json")
                if isinstance(data, dict):
                    version = data.get("version", "unknown")
                    ext_name = str(data.get("name") or "").lower()
                    display = str(data.get("displayName") or "").lower()
                    if "copilot-chat" in ext_name or "copilot chat" in display:
                        name_label = "GitHub Copilot Chat (VS Code)"
                logger.debug(f"Detected built-in VS Code {name_label} {version} at {copilot_dir}")
                return [{
                    "name": name_label,
                    "version": version,
                    "publisher": "GitHub",
                    "install_path": str(copilot_dir),
                }]
        logger.debug(f"VS Code in use under {user_home} but no built-in Copilot extension found")
        return []

    def _detect_jetbrains_all_users(self) -> List[Dict]:
        """
        Detect JetBrains Copilot for all users when running as administrator.
        """
        detected_results = []

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
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

        Checks both standard JetBrains config path (%APPDATA%\\JetBrains)
        and Toolbox installations (%LOCALAPPDATA%\\JetBrains\\Toolbox).
        """
        detected_results = []

        # Use the WindowsJetBrainsDetector to find all IDEs
        jetbrains_detector = WindowsJetBrainsDetector()
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
                        "install_path": ide.get("_config_path") or ide.get("install_path")
                    })
                    logger.info(f"Detected: GitHub Copilot ({ide['name']})")

        return detected_results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """Entry point used by the AIToolsDetector factory."""
        return self.detect_copilot()
