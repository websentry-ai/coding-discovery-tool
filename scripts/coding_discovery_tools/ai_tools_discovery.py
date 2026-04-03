#!/usr/bin/env python3
"""
AI Tools Discovery Script
Detects Cursor and Claude Code installations and extracts rules from all projects
on macOS and Windows
"""

import argparse
import json
import logging
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable

try:
    from .coding_tool_base import BaseMCPConfigExtractor
    from .coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
        WindsurfRulesExtractorFactory,
        ClineRulesExtractorFactory,
        RooRulesExtractorFactory,
        AntigravityRulesExtractorFactory,
        KiloCodeRulesExtractorFactory,
        GeminiCliRulesExtractorFactory,
        CodexRulesExtractorFactory,
        OpenCodeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
        ClaudeSettingsExtractorFactory,
        ClaudeSkillsExtractorFactory,
        CursorSettingsExtractorFactory,
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
        JetBrainsMCPConfigExtractorFactory,
        GitHubCopilotMCPConfigExtractorFactory,
        GitHubCopilotRulesExtractorFactory,
        JunieMCPConfigExtractorFactory,
        JunieRulesExtractorFactory,
        CursorCliSettingsExtractorFactory,
        CursorCliMCPConfigExtractorFactory,
        CursorCliRulesExtractorFactory,
        CursorSkillsExtractorFactory,
    )
    from .utils import send_report_to_backend, get_user_info, get_all_users_macos, get_all_users_windows, load_pending_reports, save_failed_reports, report_to_sentry, get_claude_subscription_type, get_cursor_subscription_type, sentry_cron_checkin, generate_cron_checkin_id, QUEUE_FILE
    from .logging_helpers import configure_logger, log_rules_details, log_mcp_details, log_settings_details
    from .settings_transformers import transform_settings_to_backend_format
    from .user_tool_detector import detect_tool_for_user, find_claude_binary_for_user
except ImportError:
    # Running as script directly - add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scripts.coding_discovery_tools.coding_tool_base import BaseMCPConfigExtractor
    from scripts.coding_discovery_tools.coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
        WindsurfRulesExtractorFactory,
        ClineRulesExtractorFactory,
        RooRulesExtractorFactory,
        AntigravityRulesExtractorFactory,
        KiloCodeRulesExtractorFactory,
        GeminiCliRulesExtractorFactory,
        CodexRulesExtractorFactory,
        OpenCodeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
        ClaudeSettingsExtractorFactory,
        ClaudeSkillsExtractorFactory,
        CursorSettingsExtractorFactory,
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
        JetBrainsMCPConfigExtractorFactory,
        GitHubCopilotMCPConfigExtractorFactory,
        GitHubCopilotRulesExtractorFactory,
        JunieMCPConfigExtractorFactory,
        JunieRulesExtractorFactory,
        CursorCliSettingsExtractorFactory,
        CursorCliMCPConfigExtractorFactory,
        CursorCliRulesExtractorFactory,
        CursorSkillsExtractorFactory,
    )
    from scripts.coding_discovery_tools.utils import send_report_to_backend, get_user_info, get_all_users_macos, get_all_users_windows, load_pending_reports, save_failed_reports, report_to_sentry, get_claude_subscription_type, get_cursor_subscription_type, sentry_cron_checkin, generate_cron_checkin_id, QUEUE_FILE
    from scripts.coding_discovery_tools.logging_helpers import configure_logger, log_rules_details, log_mcp_details, log_settings_details
    from scripts.coding_discovery_tools.settings_transformers import transform_settings_to_backend_format
    from scripts.coding_discovery_tools.user_tool_detector import detect_tool_for_user, find_claude_binary_for_user

# Set up logger
logger = logging.getLogger(__name__)
configure_logger()


class AIToolsDetector:
    """
    Detector for AI coding tools on macOS and Windows.
    
    Uses factory pattern to create OS-specific detectors and extractors, making it easy
    to extend support for new tools or operating systems.
    """

    def __init__(self, os_name: Optional[str] = None):
        """
        Initialize the detector.
        
        Args:
            os_name: Operating system name (defaults to current OS)
        """
        self.system = os_name or platform.system()
        
        try:
            # Initialize shared extractors
            self._device_id_extractor = DeviceIdExtractorFactory.create(self.system)
            self._tool_detectors = ToolDetectorFactory.create_all_tool_detectors(self.system)
            
            # Initialize Cursor extractors
            self._cursor_rules_extractor = CursorRulesExtractorFactory.create(self.system)
            self._cursor_mcp_extractor = CursorMCPConfigExtractorFactory.create(self.system)
            self._cursor_settings_extractor = CursorSettingsExtractorFactory.create(self.system)
            self._cursor_skills_extractor = CursorSkillsExtractorFactory.create(self.system)

            # Initialize Claude Code extractors
            self._claude_rules_extractor = ClaudeRulesExtractorFactory.create(self.system)
            self._claude_mcp_extractor = ClaudeMCPConfigExtractorFactory.create(self.system)
            self._claude_settings_extractor = ClaudeSettingsExtractorFactory.create(self.system)
            self._claude_skills_extractor = ClaudeSkillsExtractorFactory.create(self.system)
            
            # Initialize Windsurf extractors
            self._windsurf_rules_extractor = WindsurfRulesExtractorFactory.create(self.system)
            self._windsurf_mcp_extractor = WindsurfMCPConfigExtractorFactory.create(self.system)
            
            self._roo_rules_extractor = RooRulesExtractorFactory.create(self.system)
            self._roo_mcp_extractor = RooMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Cline extractors (macOS only, returns None for unsupported OS)
            self._cline_rules_extractor = ClineRulesExtractorFactory.create(self.system)
            self._cline_mcp_extractor = ClineMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Antigravity extractors (macOS and Windows)
            self._antigravity_rules_extractor = AntigravityRulesExtractorFactory.create(self.system)
            self._antigravity_mcp_extractor = AntigravityMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Kilo Code extractors (macOS only, returns None for unsupported OS)
            self._kilocode_rules_extractor = KiloCodeRulesExtractorFactory.create(self.system)
            self._kilocode_mcp_extractor = KiloCodeMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Gemini CLI extractors (macOS only, returns None for unsupported OS)
            self._gemini_cli_rules_extractor = GeminiCliRulesExtractorFactory.create(self.system)
            self._gemini_cli_mcp_extractor = GeminiCliMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Codex extractors (macOS only, returns None for unsupported OS)
            self._codex_rules_extractor = CodexRulesExtractorFactory.create(self.system)
            self._codex_mcp_extractor = CodexMCPConfigExtractorFactory.create(self.system)
            
            # Initialize OpenCode extractors (macOS only, returns None for unsupported OS)
            self._opencode_rules_extractor = OpenCodeRulesExtractorFactory.create(self.system)
            self._opencode_mcp_extractor = OpenCodeMCPConfigExtractorFactory.create(self.system)

            # Initialize JetBrains extractors (macOS only, returns None for unsupported OS)
            self._jetbrains_mcp_extractor = JetBrainsMCPConfigExtractorFactory.create(self.system)

            self._github_copilot_mcp_extractor = GitHubCopilotMCPConfigExtractorFactory.create(self.system)
            self._github_copilot_rules_extractor = GitHubCopilotRulesExtractorFactory.create(self.system)

            self._junie_mcp_extractor = JunieMCPConfigExtractorFactory.create(self.system)
            self._junie_rules_extractor = JunieRulesExtractorFactory.create(self.system)

            # Initialize Cursor CLI extractors
            self._cursor_cli_rules_extractor = CursorCliRulesExtractorFactory.create(self.system)
            self._cursor_cli_settings_extractor = CursorCliSettingsExtractorFactory.create(self.system)
            self._cursor_cli_mcp_extractor = CursorCliMCPConfigExtractorFactory.create(self.system)
        except ValueError as e:
            logger.error(f"Failed to initialize detectors: {e}")
            raise

    def get_device_id(self) -> str:
        """
        Extract unique device identifier (serial number).
        
        Returns:
            Device serial number or hostname as fallback
        """
        return self._device_id_extractor.extract_device_id()

    def detect_all_tools(self, user_home: Optional[Path] = None) -> List[Dict]:
        """
        Detect all supported AI tools.
        
        Args:
            user_home: Optional user home directory path. If provided, detects tools
                      for that specific user by checking their paths directly.
                      If None, uses current user's context.
        
        Returns:
            List of detected tools with their info
        """
        tools = []

        for detector in self._tool_detectors:
            try:
                # If user_home is provided, check user-specific paths first
                if user_home:
                    tool_info = detect_tool_for_user(detector, user_home)
                else:
                    tool_info = detector.detect()
                
                if tool_info:
                    # Handle detectors that return a list (like JetBrains)
                    if isinstance(tool_info, list):
                        tools.extend(tool_info)
                    else:
                        tools.append(tool_info)
            except Exception as e:
                logger.warning(f"Error detecting {detector.tool_name}: {e}")

        return tools


    def detect_tool(self, tool_name: str) -> Optional[Dict]:
        """
        Detect a specific tool by name.
        
        Args:
            tool_name: Name of the tool to detect (e.g., "Cursor", "Claude Code")
            
        Returns:
            Tool info dict or None if not found
        """
        for detector in self._tool_detectors:
            if detector.tool_name.lower() == tool_name.lower():
                return detector.detect()
        
        logger.warning(f"No detector found for tool: {tool_name}")
        return None

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            return self._cursor_rules_extractor.extract_all_cursor_rules()
        except Exception as e:
            logger.error(f"Error extracting Cursor rules: {e}", exc_info=True)
            return []

    def extract_all_claude_rules(self) -> Optional[Dict]:
        """
        Extract all Claude Code rules from all projects.

        Returns:
            Dict with:
            - user_rules: List of user-level rule dicts (global, scope: "user")
            - project_rules: List of project dicts with project_root and rules
            Returns None if extractor not available or on error.
        """
        try:
            if self._claude_rules_extractor:
                return self._claude_rules_extractor.extract_all_claude_rules()
            return None
        except Exception as e:
            logger.error(f"Error extracting Claude rules: {e}", exc_info=True)
            return None

    def extract_all_claude_skills(self) -> Optional[Dict]:
        """
        Extract all Claude Code skills from all projects.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
            Returns None if extractor not available or on error.
        """
        try:
            if self._claude_skills_extractor:
                return self._claude_skills_extractor.extract_all_skills()
            return None
        except Exception as e:
            logger.error(f"Error extracting Claude skills: {e}", exc_info=True)
            return None

    def extract_all_cursor_skills(self) -> Optional[Dict]:
        """
        Extract all Cursor skills from all projects.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
            Returns None if extractor not available or on error.
        """
        try:
            if self._cursor_skills_extractor:
                return self._cursor_skills_extractor.extract_all_skills()
            return None
        except Exception as e:
            logger.error(f"Error extracting Cursor skills: {e}", exc_info=True)
            return None

    def extract_all_windsurf_rules(self) -> List[Dict]:
        """
        Extract all Windsurf rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            return self._windsurf_rules_extractor.extract_all_windsurf_rules()
        except Exception as e:
            logger.error(f"Error extracting Windsurf rules: {e}", exc_info=True)
            return []

    def extract_all_roo_rules(self) -> List[Dict]:
        """
        Extract all Roo Code rules from all projects.
        """
        try:
            if self._roo_rules_extractor:
                return self._roo_rules_extractor.extract_all_roo_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Roo Code rules: {e}", exc_info=True)
            return []

    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._antigravity_rules_extractor:
                return self._antigravity_rules_extractor.extract_all_antigravity_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Antigravity rules: {e}", exc_info=True)
            return []

    def extract_all_kilocode_rules(self) -> List[Dict]:
        """
        Extract all Kilo Code rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._kilocode_rules_extractor:
                return self._kilocode_rules_extractor.extract_all_kilocode_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Kilo Code rules: {e}", exc_info=True)
            return []

    def extract_all_gemini_cli_rules(self) -> List[Dict]:
        """
        Extract all Gemini CLI rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._gemini_cli_rules_extractor:
                return self._gemini_cli_rules_extractor.extract_all_gemini_cli_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Gemini CLI rules: {e}", exc_info=True)
            return []

    def extract_all_codex_rules(self) -> List[Dict]:
        """
        Extract all Codex rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._codex_rules_extractor:
                return self._codex_rules_extractor.extract_all_codex_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Codex rules: {e}", exc_info=True)
            return []

    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects.

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._opencode_rules_extractor:
                return self._opencode_rules_extractor.extract_all_opencode_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting OpenCode rules: {e}", exc_info=True)
            return []

    def extract_all_github_copilot_rules(self, tool_name: str = None) -> List[Dict]:
        """
        Extract GitHub Copilot rules from all projects.

        Args:
            tool_name: Name of the specific tool to extract rules for (e.g., "GitHub Copilot VS Code")

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            if self._github_copilot_rules_extractor:
                return self._github_copilot_rules_extractor.extract_all_github_copilot_rules(tool_name=tool_name)
            return []
        except Exception as e:
            logger.error(f"Error extracting GitHub Copilot rules: {e}", exc_info=True)
            return []

    def extract_all_junie_rules(self) -> List[Dict]:
        """
        Extract all Junie rules from all projects.
        """
        try:
            if self._junie_rules_extractor:
                return self._junie_rules_extractor.extract_all_junie_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Junie rules: {e}", exc_info=True)
            return []

    def extract_all_cursor_cli_rules(self) -> List[Dict]:
        """
        Extract all Cursor CLI rules from all projects.
        """
        try:
            if self._cursor_cli_rules_extractor:
                return self._cursor_cli_rules_extractor.extract_all_cursor_cli_rules()
            return []
        except Exception as e:
            logger.error(f"Error extracting Cursor CLI rules: {e}", exc_info=True)
            return []

    def _process_tool_with_rules_and_mcp(
        self,
        tool: Dict,
        rules_extractor: Optional[object],
        mcp_extractor: Optional[BaseMCPConfigExtractor],
        extract_rules_func: Callable[[], List[Dict]],
        merge_mcp_func: Optional[Callable[[List[Dict], Dict[str, Dict]], None]] = None
    ) -> Dict[str, Dict]:
        """
        Helper method to process a tool that has both rules and MCP config extraction.
        
        This method handles the common pattern of:
        1. Logging processing header
        2. Extracting rules (if extractor exists)
        3. Building projects_dict from rules
        4. Logging rules details
        5. Extracting MCP configs (if extractor exists)
        6. Merging MCP configs into projects (using custom merge function if provided)
        7. Logging MCP details
        
        Args:
            tool: Tool info dict from detection
            rules_extractor: Rules extractor instance (can be None)
            mcp_extractor: MCP config extractor instance (can be None)
            extract_rules_func: Callable that extracts rules and returns List[Dict]
            merge_mcp_func: Optional custom merge function for MCP configs.
                          Defaults to _merge_mcp_configs_into_projects.
                          Should have signature: (mcp_projects: List[Dict], projects_dict: Dict[str, Dict]) -> None
            
        Returns:
            Dictionary mapping project_root to project dict
        """
        tool_name = tool.get("name", "")
        projects_dict = {}
        
        logger.info("")
        logger.info("=" * 70)
        logger.info(f"Processing: {tool_name}")
        logger.info("=" * 70)
        
        # Extract rules
        logger.info(f"  Extracting {tool_name} rules...")
        if rules_extractor:
            try:
                rules_projects = extract_rules_func()
                num_projects_with_rules = len(rules_projects)
                total_rules = sum(len(project.get("rules", [])) for project in rules_projects)
                logger.info(f"  ✓ Found {num_projects_with_rules} project(s) with {total_rules} total rule file(s)")
                
                projects_dict = {
                    project["project_root"]: {
                        "path": project["project_root"],
                        "rules": project.get("rules", [])
                    }
                    for project in rules_projects
                }
                
                # Log rules details
                if total_rules > 0:
                    log_rules_details(projects_dict, tool_name)
            except Exception as e:
                logger.error(f"Error extracting {tool_name} rules: {e}", exc_info=True)
                projects_dict = {}
        else:
            logger.info(f"  ⚠ {tool_name} rules extractor not available for this OS")
            projects_dict = {}
        
        # Extract and merge MCP configs
        logger.info(f"  Extracting {tool_name} MCP configs...")
        if mcp_extractor:
            try:
                mcp_config = mcp_extractor.extract_mcp_config()
                if mcp_config and "projects" in mcp_config:
                    num_mcp_projects = len(mcp_config["projects"])
                    logger.info(f"  ✓ Found {num_mcp_projects} project(s) with MCP config(s)")
                    
                    # Use custom merge function if provided, otherwise use default
                    merge_func = merge_mcp_func or self._merge_mcp_configs_into_projects
                    merge_func(mcp_config["projects"], projects_dict)
                    
                    # Log MCP details
                    log_mcp_details(projects_dict, tool_name)
                else:
                    logger.info("  ℹ No MCP configs found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} MCP configs: {e}", exc_info=True)
        else:
            logger.info(f"  ⚠ {tool_name} MCP extractor not available for this OS")
        
        return projects_dict

    def _process_tool_with_mcp_only(
        self,
        tool: Dict,
        mcp_extractor: Optional[BaseMCPConfigExtractor]
    ) -> Dict[str, Dict]:
        """
        Helper method to process a tool that only has MCP config extraction (no rules).
        
        Args:
            tool: Tool info dict from detection
            mcp_extractor: MCP config extractor instance (can be None)
            
        Returns:
            Dictionary mapping project_root to project dict
        """
        tool_name = tool.get("name", "")
        projects_dict = {}
        
        logger.info("")
        logger.info("=" * 70)
        logger.info(f"Processing: {tool_name}")
        logger.info("=" * 70)
        
        # Extract and merge MCP configs
        logger.info(f"  Extracting {tool_name} MCP configs...")
        if mcp_extractor:
            try:
                mcp_config = mcp_extractor.extract_mcp_config()
                if mcp_config and "projects" in mcp_config:
                    num_mcp_projects = len(mcp_config["projects"])
                    logger.info(f"  ✓ Found {num_mcp_projects} project(s) with MCP config(s)")
                    self._merge_mcp_configs_into_projects(
                        mcp_config["projects"],
                        projects_dict
                    )
                    # Log MCP details
                    log_mcp_details(projects_dict, tool_name)
                else:
                    logger.info("  ⚠ No MCP configs found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} MCP configs: {e}", exc_info=True)
        else:
            logger.info(f"  ⚠ {tool_name} MCP extractor not available for this OS")
        
        return projects_dict

    def _merge_mcp_configs_into_projects(
        self,
        mcp_projects: List[Dict],
        projects_dict: Dict[str, Dict]
    ) -> None:
        """
        Merge MCP configs into projects dictionary.
        
        Args:
            mcp_projects: List of MCP project configs
            projects_dict: Dictionary mapping project paths to project configs
        """
        total_mcp_servers = 0
        merged_count = 0
        new_count = 0
        
        for mcp_project in mcp_projects:
            project_path = mcp_project["path"]
            mcp_servers = mcp_project.get("mcpServers", [])
            num_servers = len(mcp_servers)
            total_mcp_servers += num_servers
            
            if project_path in projects_dict:
                # Merge MCP config into existing project
                projects_dict[project_path]["mcpServers"] = mcp_servers
                merged_count += 1
                logger.info(f"  Merged MCP config into existing project: {project_path} ({num_servers} MCP servers)")
                # Ensure rules field exists
                if "rules" not in projects_dict[project_path]:
                    projects_dict[project_path]["rules"] = []
            else:
                # Create new project entry with MCP config and empty rules
                projects_dict[project_path] = {
                    "path": project_path,
                    "mcpServers": mcp_servers,
                    "rules": []
                }
                new_count += 1
                logger.info(f"  Added new project from MCP config: {project_path} ({num_servers} MCP servers)")
        
        if mcp_projects:
            logger.info(f"  MCP config merge complete: {len(mcp_projects)} projects processed ({merged_count} merged, {new_count} new), {total_mcp_servers} total MCP servers")

    def _merge_claude_mcp_configs_into_projects(
        self,
        mcp_projects: List[Dict],
        projects_dict: Dict[str, Dict]
    ) -> None:
        """
        Merge Claude Code MCP configs into projects dictionary.
        
        Includes additionalMcpData extraction for Claude Code specific fields.
        
        Args:
            mcp_projects: List of MCP project configs
            projects_dict: Dictionary mapping project paths to project configs
        """
        total_mcp_servers = 0
        merged_count = 0
        new_count = 0
        
        for mcp_project in mcp_projects:
            project_path = mcp_project["path"]
            mcp_servers = mcp_project.get("mcpServers", [])
            num_servers = len(mcp_servers)
            total_mcp_servers += num_servers
            additional_mcp_data = {}
            
            # Extract Claude Code specific fields into additionalMcpData
            if mcp_project.get("mcpContextUris"):
                additional_mcp_data["mcpContextUris"] = mcp_project["mcpContextUris"]
            if mcp_project.get("enabledMcpjsonServers"):
                additional_mcp_data["enabledMcpjsonServers"] = mcp_project["enabledMcpjsonServers"]
            if mcp_project.get("disabledMcpjsonServers"):
                additional_mcp_data["disabledMcpjsonServers"] = mcp_project["disabledMcpjsonServers"]
            
            if project_path in projects_dict:
                # Merge MCP config into existing project
                projects_dict[project_path]["mcpServers"] = mcp_servers
                if additional_mcp_data:
                    projects_dict[project_path]["additionalMcpData"] = additional_mcp_data
                merged_count += 1
                logger.info(f"  Merged Claude MCP config into existing project: {project_path} ({num_servers} MCP servers)")
                # Ensure rules field exists
                if "rules" not in projects_dict[project_path]:
                    projects_dict[project_path]["rules"] = []
            else:
                # Create new project entry with MCP config and empty rules
                new_project = {
                    "path": project_path,
                    "mcpServers": mcp_servers,
                    "rules": []
                }
                if additional_mcp_data:
                    new_project["additionalMcpData"] = additional_mcp_data
                projects_dict[project_path] = new_project
                new_count += 1
                logger.info(f"  Added new project from Claude MCP config: {project_path} ({num_servers} MCP servers)")
        
        if mcp_projects:
            logger.info(f"  Claude MCP config merge complete: {len(mcp_projects)} projects processed ({merged_count} merged, {new_count} new), {total_mcp_servers} total MCP servers")

    def _merge_skills_into_projects(
        self,
        skills_projects: List[Dict],
        projects_dict: Dict[str, Dict]
    ) -> None:
        """
        Merge Claude Code skills into projects dictionary as a separate skills array.

        Skills use the same field structure as rules with additional:
        - type: "skill" to distinguish from regular rules
        - skill_name: the skill directory name

        Args:
            skills_projects: List of skill project configs (with project_root and skills)
            projects_dict: Dictionary mapping project paths to project configs
        """
        total_skills = 0
        merged_count = 0
        new_count = 0

        for skill_project in skills_projects:
            # Skills projects use "project_root" instead of "path"
            project_path = skill_project.get("project_root")
            if not project_path:
                continue

            skills = skill_project.get("skills", [])
            num_skills = len(skills)
            total_skills += num_skills

            if project_path in projects_dict:
                # Merge skills into existing project's skills array
                if "skills" not in projects_dict[project_path]:
                    projects_dict[project_path]["skills"] = []
                projects_dict[project_path]["skills"].extend(skills)
                merged_count += 1
                logger.info(f"  Merged skills into project: {project_path} ({num_skills} skills)")
            else:
                # Create new project entry with skills array
                projects_dict[project_path] = {
                    "path": project_path,
                    "rules": [],
                    "skills": skills,
                    "mcpServers": []
                }
                new_count += 1
                logger.info(f"  Added new project from skills: {project_path} ({num_skills} skills)")

        if skills_projects:
            logger.info(f"  Skills merge complete: {len(skills_projects)} projects processed ({merged_count} merged, {new_count} new), {total_skills} total skills")

    def _merge_rules_into_projects(
        self,
        rules_projects: List[Dict],
        projects_dict: Dict[str, Dict]
    ) -> None:
        """
        Merge Claude Code rules into projects dictionary.

        Args:
            rules_projects: List of rule project configs (with project_root and rules)
            projects_dict: Dictionary mapping project paths to project configs
        """
        total_rules = 0
        merged_count = 0
        new_count = 0

        for rules_project in rules_projects:
            # Rules projects use "project_root" instead of "path"
            project_path = rules_project.get("project_root")
            if not project_path:
                continue

            rules = rules_project.get("rules", [])
            num_rules = len(rules)
            total_rules += num_rules

            if project_path in projects_dict:
                # Merge rules into existing project
                if "rules" not in projects_dict[project_path]:
                    projects_dict[project_path]["rules"] = []
                projects_dict[project_path]["rules"].extend(rules)
                merged_count += 1
                logger.info(f"  Merged rules into existing project: {project_path} ({num_rules} rules)")
            else:
                # Create new project entry with rules
                projects_dict[project_path] = {
                    "path": project_path,
                    "rules": rules,
                    "skills": [],
                    "mcpServers": []
                }
                new_count += 1
                logger.info(f"  Added new project from rules: {project_path} ({num_rules} rules)")

        if rules_projects:
            logger.info(f"  Rules merge complete: {len(rules_projects)} projects processed ({merged_count} merged, {new_count} new), {total_rules} total rules")

    def _process_claude_code_tool(self, tool: Dict) -> Dict[str, Dict]:
        """
        Process Claude Code tool: extract rules, MCP configs, settings, and skills.

        This method handles Claude Code's unique structure where rules and skills
        are separated into user-level and project-level. User-level and managed
        rules/skills are placed in a home directory project entry.

        Args:
            tool: Tool info dict from detection

        Returns:
            Dictionary mapping project_root to project dict
        """
        tool_name = tool.get("name", "Claude Code")
        projects_dict = {}
        managed_rules = []

        logger.info("")
        logger.info("=" * 70)
        logger.info(f"Processing: {tool_name}")
        logger.info("=" * 70)

        # Extract rules (now returns managed_rules, user_rules and project_rules separately)
        logger.info(f"  Extracting {tool_name} rules...")
        if self._claude_rules_extractor:
            try:
                rules_result = self.extract_all_claude_rules()
                managed_rules = rules_result.get("managed_rules", []) if rules_result else []
                user_rules = rules_result.get("user_rules", []) if rules_result else []
                project_rules = rules_result.get("project_rules", []) if rules_result else []

                # Add user-level rules grouped by their project_path (user's home)
                if user_rules:
                    logger.info(f"  ✓ Found {len(user_rules)} user-level rule(s)")
                    for rule in user_rules:
                        user_home = rule.get("project_path") or str(Path.home())
                        if user_home not in projects_dict:
                            projects_dict[user_home] = {
                                "path": user_home,
                                "rules": [],
                                "skills": [],
                                "mcpServers": []
                            }
                        projects_dict[user_home]["rules"].append(rule)

                # Add managed rules to every discovered user home
                if managed_rules:
                    logger.info(f"  ✓ Found {len(managed_rules)} managed rule(s)")
                    user_homes = set()
                    for rule in user_rules:
                        if rule.get("project_path"):
                            user_homes.add(rule["project_path"])
                    if not user_homes:
                        if self.system == "Darwin":
                            for username in get_all_users_macos():
                                user_homes.add(str(Path("/Users") / username))
                        elif self.system == "Windows":
                            win_users_dir = Path(Path.home().anchor) / "Users"
                            for username in get_all_users_windows():
                                user_homes.add(str(win_users_dir / username))
                    if not user_homes:
                        user_homes.add(str(Path.home()))
                    for user_home in user_homes:
                        if user_home not in projects_dict:
                            projects_dict[user_home] = {
                                "path": user_home,
                                "rules": [],
                                "skills": [],
                                "mcpServers": []
                            }
                        projects_dict[user_home]["rules"].extend(managed_rules)

                # Merge project-level rules into projects_dict
                if project_rules:
                    num_rules_projects = len(project_rules)
                    total_rules = sum(len(project.get("rules", [])) for project in project_rules)
                    logger.info(f"  ✓ Found {num_rules_projects} project(s) with {total_rules} project-level rule(s)")
                    self._merge_rules_into_projects(project_rules, projects_dict)

                    # Log rules details
                    if total_rules > 0:
                        log_rules_details(projects_dict, tool_name)

                if not managed_rules and not user_rules and not project_rules:
                    logger.info("  ℹ No rules found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} rules: {e}", exc_info=True)
        else:
            logger.info(f"  ⚠ {tool_name} rules extractor not available for this OS")

        # Extract and merge MCP configs
        logger.info(f"  Extracting {tool_name} MCP configs...")
        if self._claude_mcp_extractor:
            try:
                mcp_config = self._claude_mcp_extractor.extract_mcp_config()
                if mcp_config and "projects" in mcp_config:
                    num_mcp_projects = len(mcp_config["projects"])
                    logger.info(f"  ✓ Found {num_mcp_projects} project(s) with MCP config(s)")
                    self._merge_claude_mcp_configs_into_projects(mcp_config["projects"], projects_dict)
                    # Log MCP details
                    log_mcp_details(projects_dict, tool_name)
                else:
                    logger.info("  ℹ No MCP configs found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} MCP configs: {e}", exc_info=True)
        else:
            logger.info(f"  ⚠ {tool_name} MCP extractor not available for this OS")

        # Extract settings
        logger.info(f"  Extracting {tool_name} settings...")
        if self._claude_settings_extractor:
            try:
                settings = self._claude_settings_extractor.extract_settings()
                logger.info(f"  Settings extraction returned: {settings is not None}, count: {len(settings) if settings else 0}")
                if settings:
                    num_settings = len(settings)
                    logger.info(f"  ✓ Found {num_settings} settings file(s)")
                    tool["_settings"] = settings
                    logger.info(f"  ✓ Stored _settings in tool dict (keys: {list(tool.keys())})")
                    log_settings_details(settings, tool_name)
                else:
                    logger.warning("  ⚠ No settings found - extract_settings() returned None or empty list")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} settings: {e}", exc_info=True)
        else:
            logger.warning(f"  ⚠ {tool_name} settings extractor not available for this OS")

        # Extract skills (Claude Code specific)
        logger.info(f"  Extracting {tool_name} skills...")
        if self._claude_skills_extractor:
            try:
                skills_result = self.extract_all_claude_skills()
                user_skills = skills_result.get("user_skills", []) if skills_result else []
                project_skills = skills_result.get("project_skills", []) if skills_result else []

                # Add user-level skills grouped by their project_path (user's home)
                if user_skills:
                    logger.info(f"  ✓ Found {len(user_skills)} user-level skill(s)")
                    for skill in user_skills:
                        user_home = skill.get("project_path") or str(Path.home())
                        if user_home not in projects_dict:
                            projects_dict[user_home] = {
                                "path": user_home,
                                "rules": [],
                                "skills": [],
                                "mcpServers": []
                            }
                        if "skills" not in projects_dict[user_home]:
                            projects_dict[user_home]["skills"] = []
                        projects_dict[user_home]["skills"].append(skill)

                    # Also distribute managed rules to any newly discovered user homes from skills
                    if managed_rules:
                        for skill in user_skills:
                            uh = skill.get("project_path")
                            if uh and uh in projects_dict:
                                existing_managed = any(
                                    r.get("scope") == "managed" for r in projects_dict[uh].get("rules", [])
                                )
                                if not existing_managed:
                                    projects_dict[uh]["rules"].extend(managed_rules)

                # Merge project-level skills into projects_dict
                if project_skills:
                    num_skills_projects = len(project_skills)
                    total_skills = sum(len(project.get("skills", [])) for project in project_skills)
                    logger.info(f"  ✓ Found {num_skills_projects} project(s) with {total_skills} project-level skill(s)")
                    self._merge_skills_into_projects(project_skills, projects_dict)

                if not user_skills and not project_skills:
                    logger.info("  ℹ No skills found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} skills: {e}", exc_info=True)
        else:
            logger.warning(f"  ⚠ {tool_name} skills extractor not available for this OS")

        return projects_dict

    def _is_project_empty(self, project: Dict) -> bool:
        """Check if a project has no meaningful data (empty mcpServers, rules, and skills)."""
        mcp_servers = project.get("mcpServers", [])
        rules = project.get("rules", [])
        skills = project.get("skills", [])
        return len(mcp_servers) == 0 and len(rules) == 0 and len(skills) == 0

    @staticmethod
    def _deduplicate_project_items(items: List[Dict]) -> List[Dict]:
        """Remove duplicate items by file_path, keeping the first occurrence."""
        seen: set = set()
        result: List[Dict] = []
        for item in items:
            fp = item.get("file_path")
            if fp is None or fp not in seen:
                if fp is not None:
                    seen.add(fp)
                result.append(item)
        return result

    def _is_jetbrains_tool(self, tool: Dict) -> bool:
        """Check if a tool is a JetBrains IDE based on its properties."""
        return "_ide_folder" in tool or "_config_path" in tool

    def _process_jetbrains_tool(self, tool: Dict) -> Dict[str, Dict]:
        """
        Process a JetBrains IDE tool: extract MCP configs for this specific IDE.

        Args:
            tool: Tool info dict from detection (contains _config_path)

        Returns:
            Dictionary mapping project_root to project dict
        """
        tool_name = tool.get("name", "")
        config_path = tool.get("_config_path", "")
        projects_dict = {}

        logger.info("")
        logger.info("=" * 70)
        logger.info(f"Processing: {tool_name}")
        logger.info("=" * 70)

        # Extract MCP configs for this specific IDE
        logger.info(f"  Extracting {tool_name} MCP configs...")
        if self._jetbrains_mcp_extractor and config_path:
            try:

                config_path_obj = Path(config_path)
                user_home = config_path_obj.parent.parent.parent.parent

                # Call the extractor's method for a single IDE
                ide_projects = self._jetbrains_mcp_extractor._extract_ide_projects(
                    config_path_obj,
                    tool.get("_ide_folder", tool_name),
                    user_home
                )

                if ide_projects:
                    logger.info(f"  ✓ Found {len(ide_projects)} project(s) with MCP/rules")

                    # Convert to projects_dict format
                    for project in ide_projects:
                        project_path = project["path"]

                        projects_dict[project_path] = {
                            "path": project_path,
                            "mcpServers": project.get("mcpServers", []),
                            "rules": project.get("rules", [])
                        }

                    log_mcp_details(projects_dict, tool_name)
                else:
                    logger.info("  ℹ No MCP configs found")
            except Exception as e:
                logger.error(f"Error extracting {tool_name} MCP configs: {e}", exc_info=True)
        else:
            logger.info(f"  ⚠ {tool_name} MCP extractor not available or config path missing")

        return projects_dict

    def filter_tool_projects_by_user(self, tool: Dict, user_home: Path) -> Dict:
        """
        Filter tool projects and permissions to only include those belonging to a specific user.
        
        Args:
            tool: Tool dict with projects populated
            user_home: Path to the user's home directory (e.g., Path("/Users/gowshik"))
            
        Returns:
            Tool dict with filtered projects and permissions
        """
        user_home_str = str(user_home)
        filtered_projects = []
        
        for project in tool.get('projects', []):
            project_path = project.get('path', '')
            # Checking if project path is under the user's home directory
            if project_path == user_home_str or project_path.startswith(user_home_str + os.sep):
                filtered_projects.append(project)

        filtered_tool = tool.copy()
        filtered_tool['projects'] = filtered_projects
        
        if 'permissions' in filtered_tool:
            perms = filtered_tool['permissions']
            settings_source = perms.get('settings_source', '')
            settings_path = perms.get('settings_path', '')
            if settings_source != 'managed' and settings_path and not (settings_path == user_home_str or settings_path.startswith(user_home_str + os.sep)):
                del filtered_tool['permissions']
        
        return filtered_tool

    def process_single_tool(self, tool: Dict) -> Dict:
        """
        Process a single tool: extract rules and MCP configs, then return tool data with projects.
        
        Args:
            tool: Tool info dict from detection
            
        Returns:
            Tool dict with projects populated
        """
        tool_name = tool.get("name", "").lower()
        projects_dict = {}

        if tool_name == "openclaw":

            tool_dict = {
                "name": tool.get("name"),
                "version": tool.get("version"),
                "install_path": tool.get("install_path"),
                "projects": [],
            }

            for key in ("platform", "is_installed", "detection_method", "is_running", "is_service"):
                if key in tool:
                    tool_dict[key] = tool[key]

            return tool_dict
        
        if "github copilot" in tool_name:
            logger.info(f"  Extracting {tool_name} rules...")
            projects_dict = {}

            original_tool_name = tool.get("name", "")

            if self._github_copilot_rules_extractor:
                try:
                    rules_projects = self._github_copilot_rules_extractor.extract_all_github_copilot_rules(
                        tool_name=original_tool_name
                    )

                    for rules_project in rules_projects:
                        project_root = rules_project.get("project_root", "")
                        rules = rules_project.get("rules", [])

                        if project_root:
                            if project_root not in projects_dict:
                                projects_dict[project_root] = {
                                    "mcpServers": [],
                                    "rules": []
                                }
                            projects_dict[project_root]["rules"] = rules

                    if rules_projects:
                        logger.info(f"  ✓ Found {len(rules_projects)} project(s) with GitHub Copilot rules")
                        log_rules_details(projects_dict, tool_name)
                    else:
                        logger.info(f"  No GitHub Copilot rules found")
                except Exception as e:
                    logger.warning(f"  Error extracting {tool_name} rules: {e}")

            # Extract MCP configs for GitHub Copilot
            logger.info(f"  Extracting {tool_name} MCP configs...")
            if self._github_copilot_mcp_extractor:
                try:
                    mcp_config = self._github_copilot_mcp_extractor.extract_mcp_config()
                    if mcp_config and "projects" in mcp_config:
                        # Merge MCP configs into projects_dict
                        for project in mcp_config["projects"]:
                            project_path = project.get("path", "")
                            if project_path:
                                if project_path not in projects_dict:
                                    projects_dict[project_path] = {
                                        "mcpServers": [],
                                        "rules": []
                                    }
                                projects_dict[project_path]["mcpServers"] = project.get("mcpServers", [])

                        # Log MCP details
                        log_mcp_details(projects_dict, tool_name)
                    else:
                        logger.info(f"  No GitHub Copilot MCP configs found")
                except Exception as e:
                    logger.warning(f"  Error extracting {tool_name} MCP config: {e}")

            # Convert projects_dict back to list format for tool_dict
            projects_list = [
                {
                    "path": path,
                    "mcpServers": data.get("mcpServers", []),
                    "rules": data.get("rules", [])
                }
                for path, data in projects_dict.items()
            ]

            tool_dict = {
                "name": tool.get("name"),  # Keep original name (e.g., "GitHub Copilot VS Code")
                "version": tool.get("version"),
                "install_path": tool.get("install_path"),
                "projects": projects_list,
            }

            return tool_dict

        # Process tools using helper methods to reduce duplication
        if tool_name == "cursor":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._cursor_rules_extractor,
                self._cursor_mcp_extractor,
                self.extract_all_cursor_rules
            )

            logger.info(f"  Extracting {tool_name} settings...")
            if self._cursor_settings_extractor:
                try:
                    settings = self._cursor_settings_extractor.extract_settings()
                    if settings:
                        logger.info(f"  ✓ Found Cursor settings")
                        tool["_settings"] = settings
                    else:
                        logger.info("  ℹ No Cursor settings found")
                except Exception as e:
                    logger.error(f"Error extracting {tool_name} settings: {e}", exc_info=True)
            else:
                logger.warning(f"  ⚠ {tool_name} settings extractor not available for this OS")

            # Extract Cursor skills
            logger.info(f"  Extracting {tool_name} skills...")
            if self._cursor_skills_extractor:
                try:
                    skills_result = self.extract_all_cursor_skills()
                    user_skills = skills_result.get("user_skills", []) if skills_result else []
                    project_skills = skills_result.get("project_skills", []) if skills_result else []

                    # Add user-level skills grouped by their project_path (user's home)
                    if user_skills:
                        logger.info(f"  ✓ Found {len(user_skills)} user-level Cursor skill(s)")
                        for skill in user_skills:
                            user_home = skill.get("project_path") or str(Path.home())
                            if user_home not in projects_dict:
                                projects_dict[user_home] = {
                                    "path": user_home,
                                    "rules": [],
                                    "skills": [],
                                    "mcpServers": []
                                }
                            if "skills" not in projects_dict[user_home]:
                                projects_dict[user_home]["skills"] = []
                            projects_dict[user_home]["skills"].append(skill)

                    # Merge project-level skills into projects_dict
                    if project_skills:
                        num_skills_projects = len(project_skills)
                        total_skills = sum(len(project.get("skills", [])) for project in project_skills)
                        logger.info(f"  ✓ Found {num_skills_projects} project(s) with {total_skills} project-level Cursor skill(s)")
                        self._merge_skills_into_projects(project_skills, projects_dict)

                    if not user_skills and not project_skills:
                        logger.info("  ℹ No Cursor skills found")
                except Exception as e:
                    logger.error(f"Error extracting {tool_name} skills: {e}", exc_info=True)
            else:
                logger.warning(f"  ⚠ {tool_name} skills extractor not available for this OS")

        elif tool_name == "claude code":
            projects_dict = self._process_claude_code_tool(tool)

        elif tool_name == "windsurf":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._windsurf_rules_extractor,
                self._windsurf_mcp_extractor,
                self.extract_all_windsurf_rules
            )
        
        elif tool_name.startswith("roo code"):
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._roo_rules_extractor,
                self._roo_mcp_extractor,
                self.extract_all_roo_rules
            )
        
        elif tool_name.startswith("cline"):
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._cline_rules_extractor,
                self._cline_mcp_extractor,
                lambda: self._cline_rules_extractor.extract_all_cline_rules() if self._cline_rules_extractor else []
            )
        
        elif tool_name == "antigravity":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._antigravity_rules_extractor,
                self._antigravity_mcp_extractor,
                self.extract_all_antigravity_rules
            )
        
        elif tool_name.replace(" ", "").lower() == "kilocode":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._kilocode_rules_extractor,
                self._kilocode_mcp_extractor,
                self.extract_all_kilocode_rules
            )
        
        elif tool_name.replace(" ", "").lower() == "geminicli":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._gemini_cli_rules_extractor,
                self._gemini_cli_mcp_extractor,
                self.extract_all_gemini_cli_rules
            )
        
        elif tool_name.replace(" ", "").lower() == "codex":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._codex_rules_extractor,
                self._codex_mcp_extractor,
                self.extract_all_codex_rules
            )
        
        elif tool_name.replace(" ", "").lower() == "opencode":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._opencode_rules_extractor,
                self._opencode_mcp_extractor,
                self.extract_all_opencode_rules
            )

        elif tool_name.lower() == "junie":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._junie_rules_extractor,
                self._junie_mcp_extractor,
                self.extract_all_junie_rules
            )

        elif tool_name.lower() == "cursor cli":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._cursor_cli_rules_extractor,
                self._cursor_cli_mcp_extractor,
                self.extract_all_cursor_cli_rules
            )

            logger.info(f"  Extracting {tool_name} settings...")
            if self._cursor_cli_settings_extractor:
                try:
                    settings = self._cursor_cli_settings_extractor.extract_settings()
                    if settings:
                        logger.info(f"  ✓ Found {len(settings)} Cursor CLI settings file(s)")
                        tool["_settings"] = settings
                        log_settings_details(settings, tool_name)
                    else:
                        logger.info("  ℹ No Cursor CLI settings found")
                except Exception as e:
                    logger.error(f"Error extracting {tool_name} settings: {e}", exc_info=True)
            else:
                logger.warning(f"  ⚠ {tool_name} settings extractor not available for this OS")

        # Check if this is a JetBrains IDE (has _ide_folder or _config_path)
        elif "_ide_folder" in tool or "_config_path" in tool:
            projects_dict = self._process_jetbrains_tool(tool)

        # Deduplicate rules and skills within each project by file_path
        for project in projects_dict.values():
            if "rules" in project:
                project["rules"] = self._deduplicate_project_items(project["rules"])
            if "skills" in project:
                project["skills"] = self._deduplicate_project_items(project["skills"])

        # Filter out empty projects (no mcpServers and no rules)
        total_projects_before_filter = len(projects_dict)
        filtered_projects = [
            project for project in projects_dict.values() 
            if not self._is_project_empty(project)
        ]
        filtered_count = total_projects_before_filter - len(filtered_projects)
        if filtered_count > 0:
            logger.info(f"  ⚠ Filtered out {filtered_count} empty project(s) (no rules and no MCP servers)")
        logger.info(f"  ✓ Final project count: {len(filtered_projects)} project(s)")
        logger.info("=" * 70)
        
        tool_dict = {
            "name": tool.get("name"),
            "version": tool.get("version"),
            "install_path": tool.get("install_path"),
            "projects": filtered_projects
        }

        if "plan" in tool:
            tool_dict["plan"] = tool["plan"]

        if "plugins" in tool:
            tool_dict["plugins"] = tool["plugins"]
            logger.info(f"  ✓ Added {len(tool['plugins'])} plugin(s) to {tool_name} report")

        if "_ide_folder" in tool:
            tool_dict["_ide_folder"] = tool["_ide_folder"]
        if "_config_path" in tool:
            tool_dict["_config_path"] = tool["_config_path"]

        logger.info(f"  Checking for settings in tool dict for {tool_name}...")
        logger.info(f"  Tool dict keys: {list(tool.keys())}")

        if "_settings" in tool:
            try:
                if tool_name == "cursor":
                    permissions = tool["_settings"]
                    logger.info(f"  ✓ Found Cursor settings (backend-ready format)")
                elif tool_name == "cursor cli":
                    # Cursor CLI settings are already in backend format (allow_rules, deny_rules at top level)
                    settings_list = tool["_settings"]
                    if settings_list:
                        # Use the first (highest precedence) settings file
                        permissions = settings_list[0] if isinstance(settings_list, list) else settings_list
                        logger.info(f"  ✓ Found Cursor CLI settings (backend-ready format)")
                    else:
                        permissions = None
                else:
                    settings_list = tool["_settings"]
                    logger.info(f"  ✓ Found _settings in tool dict, count: {len(settings_list) if settings_list else 0}")
                    permissions = transform_settings_to_backend_format(settings_list)

                if permissions:
                    tool_dict["permissions"] = permissions
                    logger.info(f"  ✓ Added permissions to {tool_name} report")
                    logger.info(f"  Permissions keys: {list(permissions.keys())}")
                else:
                    logger.warning(f"  ⚠ Permissions transformation returned None for {tool_name}")
                    logger.warning(f"  Settings that were passed: {tool['_settings']}")
            except Exception as e:
                logger.error(f"Error transforming permissions for {tool_name}: {e}", exc_info=True)
        else:
            logger.debug(f"  ℹ No _settings found in tool dict for {tool_name}")
        
        return tool_dict

    def generate_single_tool_report(self, tool: Dict, device_id: str, home_user: str, system_user: Optional[str] = None) -> Dict:
        """
        Generate a report for a single tool with user and device info.

        Args:
            tool: Tool dict with projects populated
            device_id: Device identifier
            home_user: Home directory username (the user whose data is being processed)
            system_user: Optional system user (the user running the script). If None, uses home_user.

        Returns:
            Report dictionary with single tool
        """
        # Filter out internal keys (starting with _) before sending to backend
        tool_for_report = {k: v for k, v in tool.items() if not k.startswith('_')}

        return {
            "home_user": home_user,
            "system_user": system_user or home_user,
            "device_id": device_id,
            "tools": [tool_for_report]
        }

    def generate_report(self) -> Dict:
        """
        Generate complete discovery report with tool detection and rules extraction.
        NOTE: This method is kept for backward compatibility but processes tools individually.
        
        Returns:
            Dictionary with user info, device data, and tools (with nested projects)
        """
        device_id = self.get_device_id()
        user_info = get_user_info()
        tools = self.detect_all_tools()
        
        tools_with_projects = []
        for tool in tools:
            tool_with_projects = self.process_single_tool(tool)
            tools_with_projects.append(tool_with_projects)

        return {
            "system_user": user_info,
            "device_id": device_id,
            "tools": tools_with_projects
        }


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description='AI Tools Discovery Script')
    parser.add_argument('--api-key', type=str, help='API key for authentication and report submission')
    parser.add_argument('--domain', type=str, help='Domain of the backend to send the report to')
    parser.add_argument('--app_name', type=str, help='Application name (e.g., JumpCloud)')
    args = parser.parse_args()

    if not args.api_key or not args.domain:
        print("Error: --api-key and --domain arguments are required")
        print("Please provide an API key and domain: python ai_tools_discovery.py --api-key YOUR_API_KEY --domain YOUR_DOMAIN")
        sys.exit(1)

    # Build Sentry context that persists for the whole run
    sentry_ctx = {
        "domain": args.domain,
        "app_name": args.app_name or "",
    }

    # Sentry Cron: signal script start
    cron_id = generate_cron_checkin_id()
    sentry_cron_checkin(cron_id, "in_progress")
    t_start = time.monotonic()

    try:
        detector = AIToolsDetector()

        # Get device ID once (shared across all user reports)
        device_id = detector.get_device_id()
        sentry_ctx["device_id"] = device_id

        # Track failed reports for persistence
        failed_reports = []

        # --- Drain pending reports from previous run ---
        pending = load_pending_reports()
        if pending:
            logger.info(f"Draining {len(pending)} queued report(s) from previous run...")
            for queued_report in pending:
                success, retryable = send_report_to_backend(
                    args.domain, args.api_key, queued_report, args.app_name,
                    sentry_context=sentry_ctx,
                )
                if success:
                    logger.info("  ✓ Queued report sent successfully")
                elif retryable:
                    failed_reports.append(queued_report)
                else:
                    logger.warning("  ✗ Queued report discarded (non-retryable error)")
            logger.info("")

        # Get all users for macOS/Windows, or use current user for other platforms
        if platform.system() == "Darwin":
            all_users = get_all_users_macos()
        elif platform.system() == "Windows":
            all_users = get_all_users_windows()
        else:
            all_users = []

        # If no users found, fall back to current user
        if not all_users:
            all_users = [get_user_info()]

        logger.info("=" * 60)
        logger.info("AI Tools Discovery Report")
        logger.info("=" * 60)
        logger.info(f"Device ID: {device_id}")
        logger.info("")

        # Initial detection message
        logger.info("Detecting AI tools in this device.")
        logger.info("")

        # Log users to explore
        logger.info(f"Users to process: {len(all_users)}")
        logger.info("Exploring tool detection for each user:")
        for user in all_users:
            logger.info(f"  - {user}")
        logger.info("")

        # Get system_user once (who is running the script) for audit purposes
        system_user = get_user_info()
        sentry_ctx["system_user"] = system_user

        # Detect all unique tools across all users first (to know which tools to process)
        logger.info("Detecting AI tools...")
        all_tools = []  # Store all unique tools across all users
        tools_by_user = {}  # Track which tools belong to which user

        for user in all_users:
            if platform.system() == "Darwin":
                user_home = Path(f"/Users/{user}")
            elif platform.system() == "Windows":
                user_home = Path(Path.home().anchor) / "Users" / user
            else:
                user_home = Path.home()
            logger.info(f"  Detecting tools for user: {user} (home: {user_home})")
            user_tools = detector.detect_all_tools(user_home=user_home)

            if user_tools:
                logger.info(f"    Found {len(user_tools)} tool(s) for {user}:")
                for tool in user_tools:
                    tool_name = tool.get('name', 'Unknown')
                    tool_version = tool.get('version', 'Unknown version')
                    tool_path = tool.get('install_path', 'Unknown path')
                    logger.info(f"      - {tool_name}: {tool_version} at {tool_path}")

                    # Track which user this tool belongs to
                    tool_key = f"{tool_name}:{tool_path}"
                    if tool_key not in tools_by_user:
                        tools_by_user[tool_key] = tool
                        all_tools.append(tool)
            else:
                logger.info(f"    No tools found for {user}")
            logger.info("")

        tools = all_tools
        logger.info(f"Detection complete: {len(tools)} unique tool(s) found across all users")
        logger.info("")

        # Process each tool, then explore all users for that tool and send reports
        for tool in tools:
            tool_name = tool.get('name', 'Unknown')
            sentry_ctx["tool_name"] = tool_name

            logger.info("")
            logger.info("=" * 60)
            logger.info(f"Processing tool: {tool_name}")
            logger.info("=" * 60)
            logger.info("")

            try:
                # Process this tool once (extract all rules and MCP configs for all users)
                logger.info(f"  Extracting rules and MCP configs for {tool_name}...")
                tool_with_projects = detector.process_single_tool(tool)
                logger.info(f"  ✓ Processing complete for {tool_name}")
                logger.info("")

                # Now explore all users for this tool and send reports
                tool_total_projects = 0
                tool_total_rules = 0
                tool_users_summary = []

                for user_name in all_users:
                    if platform.system() == "Darwin":
                        user_home = Path(f"/Users/{user_name}")
                    elif platform.system() == "Windows":
                        user_home = Path(Path.home().anchor) / "Users" / user_name
                    else:
                        user_home = Path.home()

                    try:
                        # Filter projects to only include this user's projects
                        tool_filtered = detector.filter_tool_projects_by_user(tool_with_projects, user_home)

                        # Detect subscription plan for Claude Code
                        if tool_name.lower() == "claude code":
                            try:
                                claude_bin = find_claude_binary_for_user(user_home)
                                if claude_bin:
                                    subscription = get_claude_subscription_type(user_name, claude_bin)
                                    if subscription:
                                        tool_filtered["plan"] = subscription
                                        logger.info(f"    Plan: {subscription}")
                                    else:
                                        logger.debug(f"    Could not detect plan for {user_name}")
                                else:
                                    logger.debug(f"    Claude binary not found for {user_name}")
                            except (PermissionError, OSError) as e:
                                logger.warning(f"    Could not detect plan for {user_name}: {e}")

                        # Detect subscription plan for Cursor / Cursor CLI
                        if tool_name.lower() in ("cursor", "cursor cli"):
                            try:
                                subscription = get_cursor_subscription_type(user_home)
                                if subscription:
                                    tool_filtered["plan"] = subscription
                                    logger.info(f"    Plan: {subscription}")
                                else:
                                    logger.debug(f"    Could not detect Cursor plan for {user_name}")
                            except Exception as e:
                                logger.warning(f"    Could not detect Cursor plan for {user_name}: {e}")

                        # Generate report for this single tool with this user's data
                        # user_name is the home_user (from /Users directory)
                        single_tool_report = detector.generate_single_tool_report(
                            tool_filtered, device_id, user_name, system_user
                        )

                        # Log tool summary for this user
                        projects = tool_filtered.get('projects', [])
                        num_projects = len(projects)
                        num_rules = sum(len(p.get('rules', [])) for p in projects)
                        num_mcp_servers = sum(len(p.get('mcpServers', [])) for p in projects)
                        tool_total_projects += num_projects
                        tool_total_rules += num_rules

                        logger.info(f"  User: {user_name}")
                        logger.info(f"    Projects: {num_projects}, Rules: {num_rules}, MCP Servers: {num_mcp_servers}")

                        tool_users_summary.append({
                            'user': user_name,
                            'projects': num_projects,
                            'rules': num_rules
                        })

                        # Log detailed summary of what's being sent
                        logger.info("")
                        logger.info("  ┌─ Report Summary ────────────────────────────────────────────────")
                        logger.info(f"  │ User: {user_name}")
                        logger.info(f"  │ Tool: {tool_name}")
                        logger.info(f"  │ Version: {tool_filtered.get('version', 'Unknown')}")
                        logger.info(f"  │ Install Path: {tool_filtered.get('install_path', 'Unknown')}")
                        logger.info(f"  │ Projects: {len(projects)}")
                        logger.info(f"  │ Total Rules: {num_rules}")
                        logger.info(f"  │ Total MCP Servers: {num_mcp_servers}")

                        # Log permissions details if present
                        if "permissions" in tool_filtered:
                            perms = tool_filtered.get("permissions", {})
                            logger.info(f"  │ Permissions: ✓ Present")
                            logger.info(f"  │   Scope: {perms.get('scope', 'unknown')}")
                            logger.info(f"  │   Path: {perms.get('settings_path', 'unknown')}")
                            logger.info(f"  │   Permission Mode: {perms.get('permission_mode', 'not set')}")
                            logger.info(f"  │   Allow Rules: {len(perms.get('allow_rules', []))}")
                            logger.info(f"  │   Deny Rules: {len(perms.get('deny_rules', []))}")
                            logger.info(f"  │   Ask Rules: {len(perms.get('ask_rules', []))}")
                            if perms.get('mcp_servers'):
                                logger.info(f"  │   MCP Servers: {len(perms.get('mcp_servers', []))}")
                            if perms.get('mcp_policies'):
                                policies = perms.get('mcp_policies', {})
                                if policies.get('allowedMcpServers') or policies.get('deniedMcpServers'):
                                    logger.info(f"  │   MCP Policies: allowed={len(policies.get('allowedMcpServers', []))}, denied={len(policies.get('deniedMcpServers', []))}")
                            logger.info(f"  │   Sandbox Enabled: {perms.get('sandbox_enabled', 'not set')}")
                        else:
                            logger.info(f"  │ Permissions: ✗ Not present")

                        logger.info("  └──────────────────────────────────────────────────────────────────")
                        logger.info("")

                        # Log the complete JSON being sent to backend
                        logger.info("  Complete JSON payload being sent to backend:")
                        logger.info("  " + "=" * 70)
                        try:
                            report_json = json.dumps(single_tool_report, indent=2)
                            for line in report_json.split('\n'):
                                logger.info(f"  {line}")
                        except Exception as e:
                            logger.warning(f"  Could not serialize report to JSON for logging: {e}")
                            logger.info(f"  Report structure: {single_tool_report}")
                        logger.info("  " + "=" * 70)
                        logger.info("")

                        # Send report to backend
                        logger.info(f"  Sending {tool_name} report for user {user_name} to backend...")

                        success, retryable = send_report_to_backend(args.domain, args.api_key, single_tool_report, args.app_name, sentry_context=sentry_ctx)
                        if success:
                            logger.info(f"  ✓ {tool_name} report for user {user_name} sent successfully")
                        else:
                            logger.error(f"  ✗ Failed to send {tool_name} report for user {user_name} to backend")
                            if retryable:
                                failed_reports.append(single_tool_report)

                        logger.info("")

                    except Exception as e:
                        logger.error(f"Error processing {tool_name} for user {user_name}: {e}", exc_info=True)
                        report_to_sentry(e, {**sentry_ctx, "phase": "process_tool_user", "tool_name": tool_name}, level="warning")
                        logger.info("")

                # Print summary for this tool
                logger.info("")
                logger.info("=" * 60)
                logger.info(f"Summary for tool: {tool_name}")
                logger.info("=" * 60)
                for user_summary in tool_users_summary:
                    logger.info(f"  - User {user_summary['user']}: {user_summary['projects']} projects, {user_summary['rules']} rule files")
                logger.info(f"Total: {tool_total_projects} projects, {tool_total_rules} rule files across {len(tool_users_summary)} user(s)")
                logger.info("=" * 60)
                logger.info("")

            except Exception as e:
                logger.error(f"Error processing tool {tool_name}: {e}", exc_info=True)
                report_to_sentry(e, {**sentry_ctx, "phase": "process_tool", "tool_name": tool_name}, level="warning")
                logger.info("")

        # --- Persist any failed reports for the next run ---
        if failed_reports:
            save_failed_reports(failed_reports)
        elif QUEUE_FILE.exists():
            # All queued reports succeeded and no new failures — clean up
            QUEUE_FILE.unlink(missing_ok=True)

        # Sentry Cron: signal success
        sentry_cron_checkin(cron_id, "ok", duration_s=time.monotonic() - t_start)

    except Exception as e:
        # Sentry Cron: signal failure
        sentry_cron_checkin(cron_id, "error", duration_s=time.monotonic() - t_start)
        report_to_sentry(e, {**sentry_ctx, "phase": "main"})
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
