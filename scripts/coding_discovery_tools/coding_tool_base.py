"""
Base classes for AI tools discovery system.

These abstract base classes define the interface for device ID extraction
and tool detection across different operating systems.
"""

import json
import logging
import shutil
import sqlite3
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, List, Union

logger = logging.getLogger(__name__)


class BaseDeviceIdExtractor(ABC):
    """Abstract base class for device ID extraction."""

    @abstractmethod
    def extract_device_id(self) -> str:
        """
        Extract unique device identifier.
        
        Returns:
            Device serial number or hostname as fallback
        """
        pass


class BaseToolDetector(ABC):
    """Abstract base class for AI tool detection."""

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        pass

    @abstractmethod
    def detect(self) -> Optional[Dict]:
        """
        Detect if the tool is installed.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        pass

    @abstractmethod
    def get_version(self) -> Optional[str]:
        """
        Extract the version of the installed tool.
        
        Returns:
            Version string or None if version cannot be determined
        """
        pass


class BaseCursorRulesExtractor(ABC):
    """Abstract base class for extracting Cursor rules from all projects."""

    @abstractmethod
    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.
        
        Searches for:
        - User-level rules: ~/.cursor/*.mdc
        - Project-level rules: **/.cursor/*.mdc (recursive)
        - Legacy format: **/.cursorrules (recursive)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseClaudeRulesExtractor(ABC):
    """Abstract base class for extracting Claude Code rules from all projects."""

    @abstractmethod
    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.
        
        Searches for:
        - Current format: **/.clauderules (recursive)
        - Current format: **/.claude/.clauderules (recursive)
        - Legacy format: **/claude.md (recursive)
        - Legacy format: **/.claude/claude.md (recursive)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseWindsurfRulesExtractor(ABC):
    """Abstract base class for extracting Windsurf rules from all projects."""

    @abstractmethod
    def extract_all_windsurf_rules(self) -> List[Dict]:
        """
        Extract all Windsurf rules from all projects on the machine.
        
        Searches for:
        - Workspace-level rules: **/.windsurf/rules/** (recursive)
        - Global rules: ~/.windsurf/global_rules.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseClineRulesExtractor(ABC):
    """Abstract base class for extracting Cline rules from all projects."""

    @abstractmethod
    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on the machine.
        
        Searches for:
        - Workspace-level rules: **/.clinerules/*.md (recursive)
        - Global rules: ~/Documents/Cline/Rules/*.md or ~/Cline/Rules/*.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseRooRulesExtractor(ABC):
    """Abstract base class for extracting Roo Code rules from all projects."""

    @abstractmethod
    def extract_all_roo_rules(self) -> List[Dict]:
        """
        Extract all Roo Code rules from all projects on the machine.

        Searches for:
        - Workspace-level rules: **/.roo/rules/*.md (recursive)
        - Mode-specific rules: **/.roo/rules-{mode}/*.md (e.g., rules-architect/)
        - Global rules: ~/Documents/Roo/Rules/*.md or ~/Roo/Rules/*.md
        """
        pass


class BaseAntigravityRulesExtractor(ABC):
    """Abstract base class for extracting Antigravity rules from all projects."""

    @abstractmethod
    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects on the machine.
        
        Searches for:
        - Project-level rules: **/.agent/rules/*.md (recursive)
        - Global rules: ~/.gemini/GEMINI.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseKiloCodeRulesExtractor(ABC):
    """Abstract base class for extracting Kilo Code rules from all projects."""

    @abstractmethod
    def extract_all_kilocode_rules(self) -> List[Dict]:
        """
        Extract all Kilo Code rules from all projects on the machine.
        
        Searches for:
        - Project-level rules: **/.kilocode/rules/*.md (recursive)
        - Global rules: ~/.kilocode/rules/*.md
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseGeminiCliRulesExtractor(ABC):
    """Abstract base class for extracting Gemini CLI rules from all projects."""

    @abstractmethod
    def extract_all_gemini_cli_rules(self) -> List[Dict]:
        """
        Extract all Gemini CLI rules from all projects on the machine.
        
        Searches for:
        - Global context: ~/.gemini/GEMINI.md
        - Project context: GEMINI.md in current working directory or any parent directory
        - Sub-directory context: GEMINI.md files in subdirectories
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseCodexRulesExtractor(ABC):
    """Abstract base class for extracting Codex rules from all projects."""

    @abstractmethod
    def extract_all_codex_rules(self) -> List[Dict]:
        """
        Extract all Codex rules from all projects on the machine.
        
        Searches for:
        - Global config: ~/.codex/config.toml (contains rules/execpolicy configuration)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseOpenCodeRulesExtractor(ABC):
    """Abstract base class for extracting OpenCode rules from all projects."""

    @abstractmethod
    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects on the machine.
        
        Searches for:
        - Global rules: ~/.config/opencode/agent/*.md
        - Project-level rules: **/.opencode/agent/*.md (recursive)
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated)
        """
        pass


class BaseGitHubCopilotRulesExtractor(ABC):
    """Abstract base class for extracting GitHub Copilot rules from all projects."""

    @abstractmethod
    def extract_all_github_copilot_rules(self, tool_name: str = None) -> List[Dict]:
        """
        Extract GitHub Copilot rules from all projects on the machine.
        """
        pass


class BaseJunieRulesExtractor(ABC):
    """Abstract base class for extracting Junie rules from all projects."""

    @abstractmethod
    def extract_all_junie_rules(self) -> List[Dict]:
        """
        Extract all Junie rules from all projects on the machine.

        Searches for:
        - Global rules: ~/.junie/*.md (any .md files in ~/.junie/ folder)
        - Project-level rules: **/.junie/*.md (any .md files in .junie folder within projects)
        """
        pass


class BaseCursorCliRulesExtractor(ABC):
    """Abstract base class for extracting Cursor CLI rules from all projects."""

    @abstractmethod
    def extract_all_cursor_cli_rules(self) -> List[Dict]:
        """
        Extract all Cursor CLI rules from all projects on the machine.

        Searches for:
        - User-level rules: ~/.cursor/rules/*.mdc, ~/.cursor/*.mdc
        - Project-level rules: **/.cursor/rules/*.mdc, **/.cursor/*.mdc, **/.cursorrules
        """
        pass


class BaseMCPConfigExtractor(ABC):
    """Abstract base class for extracting MCP configuration."""

    @abstractmethod
    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration for the tool.
        """
        pass


class BaseClaudeSettingsExtractor(ABC):
    """Abstract base class for extracting Claude Code settings (permissions)."""

    @abstractmethod
    def extract_settings(self) -> Optional[List[Dict]]:
        """
        Extract Claude Code permission settings from all sources.
        
        Searches for:
        - User Settings (global): ~/.claude/settings.json
        - Project Settings: **/.claude/settings.json and **/.claude/settings.local.json
        - Enterprise Managed: /Library/Application Support/ClaudeCode/managed-settings.json (macOS)
          or C:\\Program Files\\ClaudeCode\\managed-settings.json (Windows)
        
        Returns:
            List of settings dicts, each containing:
            - tool_name: "Claude Code"
            - settings_source: "user|project|managed"
            - settings_path: Path to the settings file
            - permissions: Dict with defaultMode, allow, deny, additionalDirectories
            - sandbox: Dict with enabled, autoAllowBashIfSandboxed
            Or None if no settings found
        """
        pass


class BaseCursorSettingsExtractor(ABC):
    """Base class for extracting Cursor IDE settings with shared parsing logic."""

    STORAGE_KEY = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"

    SECURITY_RELEVANT_KEYS = {
        "useYoloMode",
        "defaultMode2",
        "yoloEnableRunEverything",
        "yoloCommandAllowlist",
        "yoloCommandDenylist",
        "mcpAllowlist",
        "yoloDotFilesDisabled",
        "yoloDeleteFileDisabled",
        "yoloOutsideWorkspaceDisabled",
        "yoloMcpToolsDisabled",
        "playwrightProtection",
        "fullAutoRun",
        "autoFix",
        "autoApprovedModeTransitions",
        "enabledMcpServers",
        "isWebSearchToolEnabled",
        "isWebFetchToolEnabled",
        "webFetchDomainAllowlist",
    }

    MODE_SECURITY_KEYS = {"autoRun", "toolEnabled", "agentEnabled"}

    @abstractmethod
    def _get_db_path(self, user_home) -> "Path":
        """Return the OS-specific path to state.vscdb for a user."""
        pass

    @abstractmethod
    def _scan_users(self, callback) -> None:
        """Scan user directories and call callback for each user home."""
        pass

    def extract_settings(self) -> Optional[Dict]:
        """Extract Cursor IDE permission settings from SQLite database."""
        settings_list = []

        def extract_for_user(user_home: Path) -> None:
            db_path = self._get_db_path(user_home)
            if not db_path.exists():
                logger.debug(f"Cursor database not found at: {db_path}")
                return

            try:
                settings_dict = self._extract_from_database(db_path)
                if settings_dict:
                    logger.info(f"  ✓ Extracted Cursor settings from {db_path}")
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.error(f"Error extracting Cursor settings from {db_path}: {e}", exc_info=True)

        self._scan_users(extract_for_user)

        if len(settings_list) > 1:
            logger.warning(f"Found Cursor settings for {len(settings_list)} users, returning first only")

        return settings_list[0] if settings_list else None

    def _filter_raw_settings(self, composer_state: Dict) -> Dict:
        """Filter composerState to only include security-relevant keys."""
        filtered = {}

        for key in self.SECURITY_RELEVANT_KEYS:
            if key in composer_state:
                filtered[key] = composer_state[key]

        modes4 = composer_state.get("modes4", [])
        if modes4:
            filtered_modes = []
            for mode in modes4:
                if isinstance(mode, dict):
                    filtered_mode = {"name": mode.get("name", "unknown")}
                    for key in self.MODE_SECURITY_KEYS:
                        if key in mode:
                            filtered_mode[key] = mode[key]
                    filtered_modes.append(filtered_mode)
            if filtered_modes:
                filtered["modes4"] = filtered_modes

        return filtered

    def _parse_composer_state(self, composer_state: Dict, db_path) -> Dict:
        """Parse composerState into normalized backend format."""
        use_yolo_mode = composer_state.get("useYoloMode", False)
        permission_mode = "acceptEdits" if use_yolo_mode else "default"

        yolo_allowlist = composer_state.get("yoloCommandAllowlist", [])
        if not isinstance(yolo_allowlist, list):
            yolo_allowlist = []
        allow_rules = [f"Bash({cmd} *)" for cmd in yolo_allowlist if cmd and isinstance(cmd, str)]

        yolo_denylist = composer_state.get("yoloCommandDenylist", [])
        if not isinstance(yolo_denylist, list):
            yolo_denylist = []
        deny_rules = [f"Bash({cmd} *)" for cmd in yolo_denylist if cmd and isinstance(cmd, str)]

        if not composer_state.get("yoloDotFilesDisabled", False):
            deny_rules.extend(["Write(.*)", "Delete(.*)"])

        filtered_raw_settings = self._filter_raw_settings(composer_state)

        backend_settings = {
            "settings_source": "user",
            "scope": "user",
            "settings_path": str(db_path),
            "raw_settings": filtered_raw_settings,
            "permission_mode": permission_mode,
            "sandbox_enabled": None,
        }

        if allow_rules:
            backend_settings["allow_rules"] = allow_rules
        if deny_rules:
            backend_settings["deny_rules"] = deny_rules

        mcp_allowlist = composer_state.get("mcpAllowlist", [])
        if mcp_allowlist:
            backend_settings["mcp_tool_allowlist"] = mcp_allowlist

        enabled_mcp = composer_state.get("enabledMcpServers", [])
        if enabled_mcp:
            backend_settings["mcp_servers"] = enabled_mcp
            backend_settings["mcp_policies"] = {
                "allowedMcpServers": enabled_mcp,
                "deniedMcpServers": []
            }

        return backend_settings

    def _extract_from_database(self, db_path: Path) -> Optional[Dict]:
        """Extract composerState from SQLite database using a temp copy to avoid locks."""
        temp_db_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".vscdb", delete=False) as temp_db:
                temp_db_path = temp_db.name

            shutil.copy2(db_path, temp_db_path)

            with sqlite3.connect(temp_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (self.STORAGE_KEY,))
                row = cursor.fetchone()

            if not row:
                logger.debug(f"No settings found in database at: {db_path}")
                return None

            try:
                storage_data = json.loads(row[0])
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in Cursor settings: {e}")
                return None

            composer_state = storage_data.get("composerState", {})
            if not composer_state:
                logger.debug("No composerState found in storage data")
                return None

            return self._parse_composer_state(composer_state, db_path)

        except sqlite3.Error as e:
            logger.warning(f"SQLite error reading {db_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading Cursor database {db_path}: {e}")
            return None
        finally:
            if temp_db_path:
                try:
                    Path(temp_db_path).unlink(missing_ok=True)
                except Exception:
                    pass


class BaseOpenClawDetector(BaseToolDetector):
    """
    Base class for detectors that only report OpenClaw presence/absence.
    """

    @property
    def tool_name(self) -> str:
        """Return the fixed tool name for all OpenClaw detectors."""
        return "OpenClaw"

    @abstractmethod
    def detect_openclaw(self) -> Optional[Dict]:
        """
        Detect OpenClaw on the current platform.
        """
        pass

    def detect(self) -> Optional[Dict]:
        """
        Adapter to satisfy the generic `BaseToolDetector` interface.
        """
        return self.detect_openclaw()

class BaseCopilotDetector(BaseToolDetector):
    """
    Base class for detectors that only report Copilot.
    """

    @property
    def tool_name(self) -> str:
        """Return the fixed tool name for all Copilot detectors."""
        return "Copilot"

    @abstractmethod
    def detect_copilot(self) -> Union[Optional[Dict], List[Dict]]:
        """
        Detect Copilot on the current platform.
        """
        pass

    def detect(self) -> Optional[Dict]:
        """
        Adapter to satisfy the generic `BaseToolDetector` interface.
        """
        return self.detect_copilot()

    def get_version(self) -> Optional[str]:
        """
        Extract the version of the installed Copilot.
        """
        result = self.detect_copilot()
        if isinstance(result, dict):
            return result.get('version', 'unknown')
        return 'unknown'

