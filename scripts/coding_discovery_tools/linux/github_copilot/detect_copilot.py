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

        return results

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
