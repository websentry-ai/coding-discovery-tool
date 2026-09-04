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
from typing import Optional, Dict, Iterable, List, Tuple, Union

from .mcp_extraction_helpers import (
    _strip_jsonc_comments,
    _strip_trailing_commas,
    enumerate_vscode_user_files,
)
from .constants import MAX_SEARCH_DEPTH, SKIP_DIRS, is_symlink_or_junction

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
    def extract_all_claude_rules(self) -> Dict:
        """
        Extract all Claude Code rules from all projects on the machine.

        Searches for:
        - Managed: /Library/Application Support/ClaudeCode/CLAUDE.md (macOS)
                   or C:\\Program Files\\ClaudeCode\\CLAUDE.md (Windows)
        - User-level: ~/.claude/CLAUDE.md (any casing)
        - Project-level: **/.clauderules (recursive)
        - Project-level: **/.claude/.clauderules (recursive)
        - Project-level: **/CLAUDE.md (any casing, recursive)
        - Project-level: **/.claude/CLAUDE.md (any casing, recursive)
        - Local: **/CLAUDE.local.md (any casing, recursive)

        Returns:
            Dict with:
            - managed_rules: List of managed rule dicts (org-level, scope: "managed")
              Each rule has: file_path, file_name, content, size, last_modified, truncated, scope
            - user_rules: List of user-level rule dicts (global, scope: "user")
              Each rule has: file_path, file_name, content, size, last_modified, truncated, scope
            - project_rules: List of project dicts, each containing:
              - project_root: Path to the project root
              - rules: List of rule file dicts with metadata (includes scope: "project" or "local")
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
        - Global rules: ~/.roo/rules/*.md
        - Global mode-specific rules: ~/.roo/rules-{mode}/*.md
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


class BaseCopilotCliRulesExtractor(ABC):
    """Abstract base class for extracting GitHub Copilot CLI rules.

    This is for the standalone ``@github/copilot`` CLI (config under
    ``~/.copilot/``), a distinct product from the GitHub Copilot IDE
    extension/plugin. It mirrors the single-product rules bases
    (``BaseCodexRulesExtractor`` / ``BaseGeminiCliRulesExtractor``) and takes no
    ``tool_name`` argument — the CLI is one tool, not a family of IDE-coupled
    variants, so ``BaseGitHubCopilotRulesExtractor`` is intentionally not reused.
    """

    @abstractmethod
    def extract_all_copilot_cli_rules(self) -> List[Dict]:
        """
        Extract all GitHub Copilot CLI rules from all projects on the machine.

        Searches for (all paths docs-verified):
        - Global (scope "user"): ``<config_dir>/copilot-instructions.md``
        - Global (scope "user"): ``<config_dir>/instructions/**/*.instructions.md``
        - Project (scope "project"): repo-root ``.github/copilot-instructions.md``
        - Project (scope "project"): ``.github/instructions/**/*.instructions.md``
        - Project (scope "project"): repo-root ``AGENTS.md`` / ``CLAUDE.md`` /
          ``GEMINI.md`` (root only, not recursive)
        - Env (scope "user", current user only): each dir in
          ``COPILOT_CUSTOM_INSTRUCTIONS_DIRS`` contributes ``AGENTS.md`` and
          ``.github/instructions/**/*.instructions.md``

        ``<config_dir>`` honors ``COPILOT_HOME`` for the running user, else
        ``<user_home>/.copilot``.

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated, scope)
        """
        pass


class BaseCopilotCliSettingsExtractor(ABC):
    """Abstract base class for extracting GitHub Copilot CLI settings/permissions.

    For the standalone ``@github/copilot`` CLI (config under ``~/.copilot/``).
    Mirrors ``BaseClaudeSettingsExtractor`` — returns a list of per-scope settings
    dicts that the orchestrator routes through
    ``transform_settings_to_backend_format`` into the tool-level ``permissions``.
    """

    @abstractmethod
    def extract_settings(self) -> Optional[List[Dict]]:
        """
        Extract GitHub Copilot CLI durable permission settings.

        Reads the user-scope config (``<config_dir>/config.json`` and, during the
        settings migration, ``<config_dir>/settings.json``) for the keys the CLI
        actually persists: ``trusted_folders``, ``allowed_urls``, ``denied_urls``
        (snake_case on disk; camelCase tolerated). ``<config_dir>`` honors
        ``COPILOT_HOME`` for the running user, else ``<user_home>/.copilot``. When
        running as root, scans every user's home.

        Returns:
            List of per-user settings dicts (scope "user"), each with
            ``tool_name``, ``scope``, ``settings_path``, ``raw_settings`` and a
            nested ``permissions`` dict; ``None``/empty if nothing is found.
        """
        pass


class BaseAugmentRulesExtractor(ABC):
    """Abstract base class for extracting Augment Code rules from all projects.

    For Augment Code (config under ``~/.augment/``), distinct from the IDE
    Copilot/Claude surfaces. Mirrors ``BaseCopilotCliRulesExtractor`` — a single
    product, no ``tool_name`` argument.
    """

    @abstractmethod
    def extract_all_augment_rules(self) -> List[Dict]:
        """
        Extract all Augment Code rules from all projects on the machine.

        Searches for:
        - User (scope "user"): ``~/.augment/user-guidelines.md`` and
          ``~/.augment/rules/**/*.{md,mdx}``
        - Project (scope "project"): repo-root ``.augment-guidelines``,
          ``<ws>/.augment/rules/**/*.{md,mdx}``, and ``AGENTS.md`` / ``CLAUDE.md``
          discovered hierarchically (depth-bounded).

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts with metadata (file_path, file_name,
              content, size, last_modified, truncated, scope)
        """
        pass


class BaseAugmentSettingsExtractor(ABC):
    """Abstract base class for extracting Augment Code settings/permissions.

    For Augment Code (config under ``~/.augment/``). Mirrors
    ``BaseClaudeSettingsExtractor`` — returns a list of per-scope settings dicts
    routed through ``transform_settings_to_backend_format``.
    """

    @abstractmethod
    def extract_settings(self) -> Optional[List[Dict]]:
        """
        Extract Augment Code settings (permissions + full settings JSON).

        Searches for:
        - User: ``~/.augment/settings.json``
        - Managed: ``/etc/augment/settings.json``
        - Project: ``<ws>/.augment/settings.json`` and
          ``<ws>/.augment/settings.local.json`` (local scope)

        ``toolPermissions`` is parsed into ``permissions.{allow,deny,ask}`` and the
        full settings JSON (including ``hooks``) is preserved in ``raw_settings``.

        Returns:
            List of per-scope settings dicts, or ``None``/empty if nothing found.
        """
        pass


class BaseAugmentSkillsExtractor(ABC):
    """Abstract base class for extracting Augment Code skills.

    For Augment Code. Augment has no plugin system, so skills carry
    ``source="standalone"``.
    """

    @abstractmethod
    def extract_all_skills(self) -> Dict:
        """
        Extract all Augment Code skills from all projects on the machine.

        Searches:
        - User-level: ~/.augment/skills/<name>/SKILL.md, ~/.augment/commands/*.md
        - Project-level: **/.augment/commands/*.md, **/.augment/skills/<name>/SKILL.md

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope "user")
            - project_skills: List of project dicts, each containing:
              - project_root: Path to the project root
              - skills: List of skill dicts with metadata
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
    def extract_mcp_config(self, plugin_lookup: Optional[Dict] = None) -> Optional[Dict]:
        """
        Extract MCP configuration for the tool.

        Args:
            plugin_lookup: Optional dict mapping plugin install paths to provenance
                metadata. When provided, MCP servers originating from plugins are
                tagged with provenance fields.
        """
        pass


class BaseClaudeSkillsExtractor(ABC):
    """Abstract base class for extracting Claude Code skills from all projects."""

    @abstractmethod
    def extract_all_skills(self, plugin_lookup: Optional[Dict] = None) -> Dict:
        """
        Extract all Claude Code skills and commands from all projects on the machine.

        Searches for:
        - User-level skills: ~/.claude/skills/<skill-name>/SKILL.md
        - Project-level skills: **/.claude/skills/<skill-name>/SKILL.md
        - User-level commands: ~/.claude/commands/<name>.md
        - Project-level commands: **/.claude/commands/<name>.md

        The `type` field distinguishes entries: "skill" for skills, "command" for commands.

        Args:
            plugin_lookup: Optional dict mapping plugin install paths to provenance
                metadata. When provided, skills under a plugin path are tagged with
                source="plugin" and provenance fields.

        Returns:
            Dict with:
            - user_skills: List of user-level skill/command dicts (global, scope: "user")
              Each entry has: skill_name, file_path, content, size, last_modified, truncated, scope, type
            - project_skills: List of project dicts, each containing:
              - project_root: Path to the project root
              - skills: List of skill/command dicts with metadata
        """
        pass


class BaseClaudeCoworkSkillsExtractor(ABC):
    """Abstract base class for extracting Claude Cowork skills from Claude Desktop.

    Claude Cowork skills live in Claude Desktop's Application Support tree
    (macOS) or AppData tree (Windows), under ``local-agent-mode-sessions/``.
    They are distinct from Claude Code skills (which live under ``~/.claude/``).
    """

    @abstractmethod
    def extract_all_skills(self) -> Dict:
        """
        Extract all Claude Cowork skills discoverable on disk.

        Cowork has no concept of a "project" — all skills are effectively
        user-level. The returned shape still mirrors the Claude Code extractor
        so the rest of the pipeline (``process_single_tool`` orchestration,
        backend ingestion) can reuse a single code path.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope: "user").
              Each entry has: skill_name, file_path, file_name, project_path,
              content, size, last_modified, truncated, scope, type.
            - project_skills: Always an empty list — Cowork has no project scope.
        """
        pass


class BaseCursorSkillsExtractor(ABC):
    """Abstract base class for extracting Cursor skills from all projects."""

    @abstractmethod
    def extract_all_skills(self, plugin_lookup: Optional[Dict] = None) -> Dict:
        """
        Extract all Cursor skills from all projects on the machine.

        Searches for:
        - User-level skills: ~/.cursor/skills/<skill-name>/SKILL.md
        - Project-level skills: **/.cursor/skills/<skill-name>/SKILL.md

        The `type` field distinguishes entries: "skill" for skills.

        Args:
            plugin_lookup: Optional dict mapping plugin install paths to provenance
                metadata. When provided, skills under a plugin path are tagged with
                source="plugin" and provenance fields.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
              Each entry has: skill_name, file_path, content, size, last_modified, truncated, scope, type
            - project_skills: List of project dicts, each containing:
              - project_root: Path to the project root
              - skills: List of skill dicts with metadata
        """
        pass


class BaseClineSkillsExtractor(ABC):
    """Abstract base class for extracting Cline skills from all projects."""

    @abstractmethod
    def extract_all_skills(self) -> Dict:
        """
        Extract all Cline skills from all projects on the machine.

        Searches for:
        - User-level skills: ~/.cline/skills/<skill-name>/SKILL.md
        - Project-level skills: **/.cline/skills/<skill-name>/SKILL.md
        - Project-level skills: **/.clinerules/skills/<skill-name>/SKILL.md
        - Project-level skills: **/.claude/skills/<skill-name>/SKILL.md

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
              Each entry has: skill_name, file_path, content, size, last_modified, truncated, scope, type
            - project_skills: List of project dicts, each containing:
              - project_root: Path to the project root
              - skills: List of skill dicts with metadata
        """
        pass


class BaseCopilotCliSkillsExtractor(ABC):
    """Abstract base class for extracting GitHub Copilot CLI skills.

    For the standalone ``@github/copilot`` CLI. Copilot CLI has no plugin system,
    so skills carry ``source="standalone"``.
    """

    @abstractmethod
    def extract_all_skills(self) -> Dict:
        """
        Extract all GitHub Copilot CLI skills from all projects on the machine.

        Each skill is a subdirectory containing a ``SKILL.md``. Searches:
        - User-level: ~/.copilot/skills/<name>/SKILL.md, ~/.agents/skills/<name>/SKILL.md
        - Project-level: **/.github/skills/<name>/SKILL.md,
          **/.claude/skills/<name>/SKILL.md, **/.agents/skills/<name>/SKILL.md

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope "user")
              Each entry has: skill_name, file_path, file_name, content, size,
              last_modified, truncated, scope, type, source, project_path
            - project_skills: List of project dicts, each containing:
              - project_root: Path to the project root
              - skills: List of skill dicts with metadata
        """
        pass


class _BaseAgentSkillsExtractor(ABC):
    """Shared base for standalone SKILL.md (Agent Skills) extractors.

    Covers the tools that adopted the cross-vendor ``SKILL.md`` standard
    (agentskills.io) without a plugin system, so their skills always carry
    ``source="standalone"``. Every subclass returns the same shape as the Claude
    Code / Cline extractors so ``_extract_and_merge_tool_skills`` can drive them
    all through one code path. Concrete per-tool subclasses exist (rather than a
    single shared class) so each factory keeps a meaningful return type and each
    tool documents its own discovery paths.
    """

    @abstractmethod
    def extract_all_skills(self) -> Dict:
        """Extract all skills for this tool from every user + project on the machine.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (scope "user"), each with
              a ``project_path`` naming the owning user's home.
            - project_skills: List of ``{project_root, skills}`` dicts.
        """
        pass


class BaseCodexSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract OpenAI Codex skills. Paths verified against the CLI itself
    (``codex app-server`` -> ``skills/list``), not just the docs page: user
    ``~/.codex/skills/<name>/SKILL.md`` (i.e. ``$CODEX_HOME/skills``, where Codex's
    own skill-creator/skill-installer write) plus the ``~/.agents/skills`` alias;
    project ``<repo>/.agents/skills/<name>/SKILL.md``. OpenAI built-ins under
    ``~/.codex/skills/.system/`` and plugin-bundled skills under
    ``~/.codex/plugins/cache/`` are vendor content and intentionally excluded."""


class BaseGeminiCliSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Gemini CLI skills. Paths: user ``~/.gemini/skills/`` (+ ``~/.agents/skills/``
    alias); project ``.gemini/skills/`` (+ ``.agents/skills/`` alias)."""


class BaseOpenCodeSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract OpenCode skills. Paths: user ``~/.config/opencode/skills/``,
    ``~/.claude/skills/``, ``~/.agents/skills/``; project ``.opencode/skills/``,
    ``.claude/skills/``, ``.agents/skills/``."""


class BaseJunieSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Junie skills. Paths: user ``~/.junie/skills/<name>/``; project
    ``<root>/.junie/skills/<name>/``."""


class BaseKiloCodeSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Kilo Code skills. Paths: user ``~/.kilo/skills/``; project
    ``.kilo/skills/`` and ``.agents/skills`` (default) + ``.claude/skills`` (compat)."""


class BaseReplitSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Replit skills. Project-scope only: ``.agents/skills/<name>/SKILL.md``
    (no local user/global path — global skills live server-side in Workspace Settings)."""


class BaseRooSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Roo Code skills. Paths: user ``~/.roo/skills/`` + ``~/.agents/skills/``;
    project ``.roo/skills/`` + ``.agents/skills/``. Also mode-specific ``skills-{mode}/``
    dirs alongside ``skills/`` (mirrors Roo's ``rules-{mode}/`` convention)."""


class BaseWindsurfSkillsExtractor(_BaseAgentSkillsExtractor):
    """Extract Windsurf (Cascade) skills. Paths (docs.devin.ai/desktop/cascade/skills):
    user ``~/.codeium/windsurf/skills/`` + ``~/.agents/skills/`` + ``~/.claude/skills/``;
    project ``.windsurf/skills/`` + ``.agents/skills/`` + ``.claude/skills/`` (``.claude``
    when Claude Code config reading is enabled). The docs do NOT list
    ``.github``/``.cursor``/``.codex`` as Windsurf skill dirs."""


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

    @abstractmethod
    def _get_user_permissions_path(self, user_home) -> "Path":
        """Return the global per-user Cursor permissions file: ~/.cursor/permissions.json."""
        pass

    @abstractmethod
    def _iter_workspace_permissions_files(self, user_home) -> Iterable["Path"]:
        """Yield every <workspace>/.cursor/permissions.json under this user.

        Excludes the global ~/.cursor/permissions.json (yielded separately by
        ``_get_user_permissions_path``) so it is never double-counted.

        Note: the team-admin dashboard is the ultimate authority ceiling for
        Cursor permissions (admin dashboard > permissions.json > IDE/SQLite),
        but it is cloud-only and unreadable locally, so it is intentionally not
        represented here.
        """
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
                settings_dict = self._extract_from_database(db_path, user_home)
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

    def _read_permissions_json(self, path) -> Optional[Dict]:
        """Read and parse a Cursor ``permissions.json`` file, JSONC-tolerant.

        Strips // and /* */ comments and trailing commas before parsing. Returns
        the parsed dict, or ``None`` on any failure (missing file, bad JSON, OS
        error, non-object root). Never raises.
        """
        try:
            raw = Path(path).read_text(encoding="utf-8")
            cleaned = _strip_trailing_commas(_strip_jsonc_comments(raw))
            data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.debug(f"Could not read Cursor permissions file {path}: {e}")
            return None

    def _collect_permissions_files(self, user_home) -> Tuple[Optional[Dict], List[Dict]]:
        """Collect parsed permissions dicts for a user.

        Reads the single global ``~/.cursor/permissions.json`` plus every
        per-workspace file. Each read is independently guarded so one unreadable
        workspace file cannot poison the others.

        Returns:
            ``(user_dict_or_None, [workspace_dicts])`` — workspace dicts in walk
            order, only successfully-parsed objects included.
        """
        user_dict = self._read_permissions_json(self._get_user_permissions_path(user_home))

        workspace_dicts: List[Dict] = []
        try:
            for ws_path in self._iter_workspace_permissions_files(user_home):
                parsed = self._read_permissions_json(ws_path)
                if parsed is not None:
                    workspace_dicts.append(parsed)
        except Exception as e:
            logger.debug(f"Error iterating workspace Cursor permissions for {user_home}: {e}")

        return user_dict, workspace_dicts

    @staticmethod
    def _dedupe_preserve_order(values: Iterable) -> List:
        """Return list values with order preserved and duplicates removed."""
        seen = set()
        result = []
        for value in values:
            try:
                if value in seen:
                    continue
                seen.add(value)
            except TypeError:
                if value in result:
                    continue
            result.append(value)
        return result

    def _merge_permissions_fields(self, user_dict, workspace_dicts) -> Dict:
        """Merge the known permissions fields across user + workspace files.

        For each known field, concatenates the user file first then each
        workspace in walk order, with order-preserving de-dupe. ``autoRun`` is an
        object: its documented ``allow_instructions`` / ``block_instructions``
        arrays are concatenated independently across files, while any other
        sub-keys are preserved via a shallow merge (last file wins). Unknown
        top-level keys are ignored.

        Returns ``{"mcpAllowlist": [...]|None, "terminalAllowlist": [...]|None,
        "autoRun": {...}|None}`` where ``None`` means no file spoke to that field.
        """
        sources = []
        if user_dict is not None:
            sources.append(user_dict)
        sources.extend(workspace_dicts)

        def merge_list_field(field_name):
            saw = False
            collected = []
            for src in sources:
                value = src.get(field_name)
                if isinstance(value, list):
                    saw = True
                    collected.extend(value)
            return self._dedupe_preserve_order(collected) if saw else None

        merged_auto_run = None
        collected_instructions = {"allow_instructions": [], "block_instructions": []}
        for src in sources:
            auto_run = src.get("autoRun")
            if not isinstance(auto_run, dict):
                continue
            if merged_auto_run is None:
                merged_auto_run = {}
            # Shallow-merge to preserve unknown sub-keys (later file wins).
            merged_auto_run.update(auto_run)
            for nested_key in ("allow_instructions", "block_instructions"):
                nested = auto_run.get(nested_key)
                if isinstance(nested, list):
                    collected_instructions[nested_key].extend(nested)

        if merged_auto_run is not None:
            # Overwrite the documented arrays with their concatenated+deduped
            # values, leaving any other preserved sub-keys untouched.
            for nested_key in ("allow_instructions", "block_instructions"):
                merged_auto_run[nested_key] = self._dedupe_preserve_order(
                    collected_instructions[nested_key]
                )

        return {
            "mcpAllowlist": merge_list_field("mcpAllowlist"),
            "terminalAllowlist": merge_list_field("terminalAllowlist"),
            "autoRun": merged_auto_run,
        }

    def _apply_permissions_json_override(self, backend_settings, user_home) -> Dict:
        """Layer ``permissions.json`` over the SQLite-derived backend record.

        Per-field REPLACE semantics: a field present in any permissions.json wins
        over the SQLite-derived value for that field; silent fields are untouched.
        When no permissions.json file speaks to any known field, returns
        ``backend_settings`` unchanged (byte-identical guarantee).
        """
        user_dict, workspace_dicts = self._collect_permissions_files(user_home)
        merged = self._merge_permissions_fields(user_dict, workspace_dicts)

        merged_mcp = merged["mcpAllowlist"]
        merged_terminal = merged["terminalAllowlist"]
        merged_auto_run = merged["autoRun"]

        if merged_mcp is None and merged_terminal is None and merged_auto_run is None:
            return backend_settings

        if merged_mcp is not None:
            backend_settings["mcp_tool_allowlist"] = merged_mcp

        if merged_terminal is not None:
            backend_settings["allow_rules"] = [
                f"Bash({cmd}*)" for cmd in merged_terminal if cmd and isinstance(cmd, str)
            ]

        if merged_auto_run is not None:
            raw_settings = backend_settings.get("raw_settings")
            if isinstance(raw_settings, dict):
                raw_settings["autoRun"] = merged_auto_run

        applied_fields = [
            name
            for name, value in (
                ("mcp", merged_mcp),
                ("terminal", merged_terminal),
                ("autorun", merged_auto_run),
            )
            if value is not None
        ]
        logger.info(f"  ✓ Applied permissions.json override for fields: {', '.join(applied_fields)}")

        return backend_settings

    def _extract_from_database(self, db_path: Path, user_home: Path) -> Optional[Dict]:
        """Extract composerState from SQLite database using a temp copy to avoid locks.

        After parsing the SQLite-derived backend record, layers Cursor's
        ``permissions.json`` files (global + per-workspace) on top as a per-field
        override (see ``_apply_permissions_json_override``).
        """
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

            backend = self._parse_composer_state(composer_state, db_path)
            return self._apply_permissions_json_override(backend, user_home)

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


class BaseGitHubCopilotSettingsExtractor(ABC):
    """Extract VS Code GitHub Copilot agent-mode permissions from ``settings.json``.

    Copilot's agent permissions have no dedicated file — they live in VS Code's
    ``settings.json`` (JSONC): the user-scope file plus any
    ``<workspace>/.vscode/settings.json``. This reads the security-relevant
    auto-approve / terminal / MCP keys and emits the same backend-ready record the
    Cursor extractor does (``permission_mode`` + ``allow_rules`` / ``deny_rules`` /
    ``mcp_*``), so it routes as tool-level permissions with no backend change.
    """

    # Keys sourced from Microsoft's enterprise AI-settings reference and the
    # agent-mode docs. A file with none of these carries no permission signal.
    SECURITY_RELEVANT_KEYS = {
        "chat.tools.global.autoApprove", "chat.tools.autoApprove",
        "chat.tools.eligibleForAutoApproval",
        "chat.tools.terminal.enableAutoApprove", "chat.tools.terminal.autoApprove",
        "chat.tools.urls.autoApprove",
        "github.copilot.chat.agent.autoApproveFileChanges",
        "github.copilot.chat.agent.autoApproveTerminal",
        "github.copilot.chat.agent.terminalCommands.blocklist",
        "chat.agent.enabled", "github.copilot.chat.agent.enabled",
        "chat.agent.sandbox.enabled",
        "chat.agent.networkFilter", "chat.agent.allowedNetworkDomains",
        "chat.agent.deniedNetworkDomains",
        "chat.mcp.access", "chat.mcp.allowedServers", "chat.mcp.deniedServers",
        "github.copilot.chat.claudeAgent.enabled",
    }

    # A truthy global auto-approve removes every confirmation (bypass); auto-approving
    # only file edits maps to acceptEdits; anything else is the default gated mode.
    _GLOBAL_AUTOAPPROVE_KEYS = ("chat.tools.global.autoApprove", "chat.tools.autoApprove")

    @abstractmethod
    def _scan_users(self, callback) -> None:
        """Call ``callback(user_home)`` for each user home to scan (all users under
        a root scan, else just the current user)."""

    @abstractmethod
    def _user_config_dirs(self, user_home) -> List[Path]:
        """Return this user's VS Code ``User`` config dirs (stable Code first, then
        Insiders) — the parents of ``settings.json`` and ``profiles/``."""

    def _iter_user_settings_files(self, user_home):
        """Yield the default-profile ``settings.json`` plus every named profile's
        ``profiles/<id>/settings.json``, for each channel (stable + Insiders), via
        the same shared VS Code profile enumeration the ``mcp.json`` discovery uses.
        Each profile is its own permission surface — a YOLO named profile is as real
        a risk as the default — so all are read (not first-wins) and merged to the
        most permissive posture."""
        for user_dir in self._user_config_dirs(Path(user_home)):
            for path in enumerate_vscode_user_files(user_dir, "settings.json"):
                yield path

    def _iter_workspace_settings_files(self, user_home) -> Iterable[Path]:
        """Yield every ``<workspace>/.vscode/settings.json`` under this user.

        ``.vscode`` is in ``SKIP_DIRS`` (the project walk treats it as a config
        dir, not a subtree to descend), so the general walk never reaches it —
        exactly as the workspace ``mcp.json`` discovery has to. This walk exempts
        the ``.vscode`` leaf: it is checked for ``settings.json`` but never
        descended, every other ``SKIP_DIRS`` entry is pruned, links are not
        followed, and depth is bounded."""
        found: List[Path] = []
        self._walk_workspace_settings(Path(user_home), found, current_depth=0)
        return found

    def _walk_workspace_settings(self, current_dir: Path, found: List[Path],
                                 current_depth: int = 0) -> None:
        if current_depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current_dir.iterdir():
                try:
                    if not item.is_dir():
                        continue
                    if item.name == ".vscode":
                        settings = item / "settings.json"
                        if settings.is_file():
                            found.append(settings)
                        continue  # never descend the config dir
                    if item.name in SKIP_DIRS or is_symlink_or_junction(item):
                        continue
                    self._walk_workspace_settings(item, found, current_depth + 1)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            return

    def extract_settings(self) -> Optional[Dict]:
        """Return one backend-ready permission record (user scope, with any
        workspace ``.vscode/settings.json`` allow/deny/MCP lists merged in), or
        None when no Copilot permission setting is present anywhere."""
        records: List[Dict] = []

        def extract_for_user(user_home) -> None:
            try:
                rec = self._extract_for_user(Path(user_home))
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.debug(f"Error extracting Copilot settings for {user_home}: {e}")

        self._scan_users(extract_for_user)
        if not records:
            return None
        if len(records) > 1:
            logger.warning(f"Found Copilot settings for {len(records)} users, returning first only")
        return records[0]

    def _extract_for_user(self, user_home: Path) -> Optional[Dict]:
        records = []
        # User scope: default profile + every named profile, each channel.
        for path in self._iter_user_settings_files(user_home):
            data = self._parse_jsonc(path)
            if data is None:
                continue
            record = self._build_record(data, path, "user")
            if record:
                logger.info(f"  ✓ Extracted Copilot settings from {path}")
                records.append(record)
        # Project scope: every <workspace>/.vscode/settings.json.
        for path in self._iter_workspace_settings_files(user_home):
            data = self._parse_jsonc(path)
            if data is None:
                continue
            record = self._build_record(data, path, "project")
            if record:
                records.append(record)

        if not records:
            return None
        return self._merge_records(records[0], records[1:])

    @staticmethod
    def _parse_jsonc(path: Path) -> Optional[Dict]:
        """Leniently parse a VS Code JSONC settings file (comments + trailing commas
        tolerated). Returns the dict, or None if missing/unreadable/not a dict.
        Never raises — this runs on customer machines."""
        try:
            if not path.is_file():
                return None
            # utf-8-sig strips a leading BOM (some Windows editors write one) so
            # json.loads doesn't choke on it; plain UTF-8 is read unchanged.
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            data = json.loads(_strip_trailing_commas(_strip_jsonc_comments(raw)))
            return data if isinstance(data, dict) else None
        except (PermissionError, OSError) as e:
            logger.debug(f"Permission/OS error reading {path}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Could not parse {path}: {e}")
            return None

    def _build_record(self, data: Dict, path: Path, scope: str) -> Optional[Dict]:
        raw_settings = {k: data[k] for k in self.SECURITY_RELEVANT_KEYS if k in data}
        if not raw_settings:
            return None  # nothing security-relevant here → no row
        allow_rules, deny_rules = self._terminal_rules(data)
        mcp_allow, mcp_deny = self._mcp_lists(data)
        record = {
            "settings_source": scope,
            "scope": scope,
            "settings_path": str(path),
            "raw_settings": raw_settings,
            "permission_mode": self._permission_mode(data),
            "sandbox_enabled": self._sandbox_enabled(data),
        }
        if allow_rules:
            record["allow_rules"] = allow_rules
        if deny_rules:
            record["deny_rules"] = deny_rules
        if mcp_allow:
            record["mcp_tool_allowlist"] = mcp_allow
        if mcp_allow or mcp_deny:
            record["mcp_policies"] = {"allowedMcpServers": mcp_allow, "deniedMcpServers": mcp_deny}
        return record

    def _permission_mode(self, data: Dict) -> str:
        if any(data.get(k) is True for k in self._GLOBAL_AUTOAPPROVE_KEYS):
            return "bypassPermissions"
        if data.get("github.copilot.chat.agent.autoApproveFileChanges") is True:
            return "acceptEdits"
        return "default"

    @staticmethod
    def _sandbox_enabled(data: Dict):
        val = data.get("chat.agent.sandbox.enabled")
        if isinstance(val, str):
            return val.lower() == "on"
        if isinstance(val, bool):
            return val
        return None

    @staticmethod
    def _clean_terminal_pattern(pattern: str) -> str:
        """Strip a wrapping ``/…/`` regex so the rule reads as the command."""
        p = pattern.strip()
        if len(p) >= 2 and p.startswith("/") and p.endswith("/"):
            p = p[1:-1]
        return p

    def _terminal_rules(self, data: Dict) -> Tuple[List[str], List[str]]:
        allow, deny = [], []
        auto = data.get("chat.tools.terminal.autoApprove")
        if isinstance(auto, dict):
            for pattern, verdict in auto.items():
                if not isinstance(pattern, str) or not pattern:
                    continue
                rule = f"Bash({self._clean_terminal_pattern(pattern)} *)"
                if verdict is True:
                    allow.append(rule)
                elif verdict is False:
                    deny.append(rule)
        blocklist = data.get("github.copilot.chat.agent.terminalCommands.blocklist")
        if isinstance(blocklist, list):
            for cmd in blocklist:
                if isinstance(cmd, str) and cmd:
                    deny.append(f"Bash({cmd} *)")
        return self._dedupe(allow), self._dedupe(deny)

    @staticmethod
    def _mcp_lists(data: Dict) -> Tuple[List[str], List[str]]:
        def names(value) -> List[str]:
            out = []
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item:
                        out.append(item)
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("id") or item.get("url") or item.get("command")
                        if isinstance(name, str) and name:
                            out.append(name)
            return out
        allow = names(data.get("chat.mcp.allowedServers"))
        deny = names(data.get("chat.mcp.deniedServers"))
        return BaseGitHubCopilotSettingsExtractor._dedupe(allow), BaseGitHubCopilotSettingsExtractor._dedupe(deny)

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        seen, out = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _merge_records(self, base: Dict, others: List[Dict]) -> Dict:
        """Union the allow/deny/MCP lists across every profile and workspace record
        — concatenated base-first, order-preserving de-dupe — and escalate the mode
        to the most permissive seen, so one YOLO profile or workspace surfaces even
        when the default profile is locked down."""
        if not others:
            return base
        merged = dict(base)
        order = {"default": 0, "acceptEdits": 1, "bypassPermissions": 2}
        for rec in others:
            for field in ("allow_rules", "deny_rules", "mcp_tool_allowlist"):
                extra = rec.get(field)
                if extra:
                    merged[field] = self._dedupe(merged.get(field, []) + extra)
            if order.get(rec.get("permission_mode"), 0) > order.get(merged.get("permission_mode"), 0):
                merged["permission_mode"] = rec["permission_mode"]
            base_pol = merged.get("mcp_policies")
            rec_pol = rec.get("mcp_policies")
            if rec_pol:
                if base_pol:
                    merged["mcp_policies"] = {
                        "allowedMcpServers": self._dedupe(base_pol.get("allowedMcpServers", []) + rec_pol.get("allowedMcpServers", [])),
                        "deniedMcpServers": self._dedupe(base_pol.get("deniedMcpServers", []) + rec_pol.get("deniedMcpServers", [])),
                    }
                else:
                    merged["mcp_policies"] = rec_pol
        return merged


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

