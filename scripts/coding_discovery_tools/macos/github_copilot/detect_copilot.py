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


class MacOSCopilotDetector(BaseCopilotDetectorBase):
    """
    Detects GitHub Copilot across VS Code and all JetBrains IDEs on macOS.
    """
    tool_name: str = "GitHub Copilot"

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
                    "name": "GitHub Copilot VS Code",
                    "version": ext.get('version', 'unknown'),
                    "publisher": "GitHub",
                    "install_path": str(vscode_ext_path.parent)
                })

        return results

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
                        "name": f"GitHub Copilot {ide['name']}",
                        "version": ide.get("version", "unknown"),
                        "publisher": "GitHub",
                        "ide": ide['name'],
                        "install_path": ide.get("config_path") or ide.get("install_path")
                    })

        return detected_results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """Entry point used by the AIToolsDetector factory."""
        return self.detect_copilot()
