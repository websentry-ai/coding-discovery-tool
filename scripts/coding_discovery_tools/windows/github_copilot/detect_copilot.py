import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseCopilotDetector
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


class WindowsGitHubCopilotDetector(BaseCopilotDetector):
    """
    Detects GitHub Copilot across VS Code and all JetBrains IDEs on Windows.
    """
    tool_name: str = "GitHub Copilot"

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

        Scans %USERPROFILE%\\.vscode\\extensions for folders starting with github.copilot*.
        """
        results = []
        vscode_ext_dir = user_home / ".vscode" / "extensions"

        if not vscode_ext_dir.exists():
            logger.debug(f"VS Code extensions directory not found: {vscode_ext_dir}")
            return results

        try:
            # Look for github.copilot* directories
            copilot_dirs = list(vscode_ext_dir.glob("github.copilot*"))

            for copilot_dir in copilot_dirs:
                if not copilot_dir.is_dir():
                    continue

                version = "unknown"
                pkg_json = copilot_dir / "package.json"

                if pkg_json.exists():
                    data = _load_jsonc(pkg_json)
                    if data:
                        version = data.get('version', 'unknown')

                if version == "unknown" and "-" in copilot_dir.name:
                    try:
                        version = copilot_dir.name.rsplit('-', 1)[1]
                    except IndexError:
                        pass

                ext_name = "GitHub Copilot VS Code"
                if "copilot-chat" in copilot_dir.name.lower():
                    ext_name = "GitHub Copilot Chat VS Code"

                results.append({
                    "name": ext_name,
                    "version": version,
                    "publisher": "GitHub",
                    "install_path": str(copilot_dir)
                })
                logger.info(f"Detected: {ext_name} v{version} at {copilot_dir}")

        except (PermissionError, OSError) as e:
            logger.debug(f"Error scanning VS Code extensions: {e}")

        return results

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
                        "name": f"GitHub Copilot {ide['name']}",
                        "version": ide.get("version", "unknown"),
                        "publisher": "GitHub",
                        "ide": ide['name'],
                        "install_path": ide.get("_config_path") or ide.get("install_path")
                    })
                    logger.info(f"Detected: GitHub Copilot for {ide['name']}")

        return detected_results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """Entry point used by the AIToolsDetector factory."""
        return self.detect_copilot()
