#!/usr/bin/env python3
"""
AI Tools Discovery Script
Detects Cursor and Claude Code installations and extracts rules from all projects
on macOS and Windows
"""

import argparse
import json
import logging
import platform
import sys
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
        AntigravityRulesExtractorFactory,
        KiloCodeRulesExtractorFactory,
        GeminiCliRulesExtractorFactory,
        CodexRulesExtractorFactory,
        OpenCodeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
        ClaudeSettingsExtractorFactory,
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
    )
    from .utils import send_report_to_backend, get_user_info, get_all_users_macos
    from .logging_helpers import configure_logger, log_rules_details, log_mcp_details, log_settings_details
    from .settings_transformers import transform_settings_to_backend_format
    from .user_tool_detector import detect_tool_for_user
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
        AntigravityRulesExtractorFactory,
        KiloCodeRulesExtractorFactory,
        GeminiCliRulesExtractorFactory,
        CodexRulesExtractorFactory,
        OpenCodeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
        ClaudeSettingsExtractorFactory,
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
    )
    from scripts.coding_discovery_tools.utils import send_report_to_backend, get_user_info, get_all_users_macos
    from scripts.coding_discovery_tools.logging_helpers import configure_logger, log_rules_details, log_mcp_details, log_settings_details
    from scripts.coding_discovery_tools.settings_transformers import transform_settings_to_backend_format
    from scripts.coding_discovery_tools.user_tool_detector import detect_tool_for_user

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
            
            # Initialize Claude Code extractors
            self._claude_rules_extractor = ClaudeRulesExtractorFactory.create(self.system)
            self._claude_mcp_extractor = ClaudeMCPConfigExtractorFactory.create(self.system)
            self._claude_settings_extractor = ClaudeSettingsExtractorFactory.create(self.system)
            
            # Initialize Windsurf extractors
            self._windsurf_rules_extractor = WindsurfRulesExtractorFactory.create(self.system)
            self._windsurf_mcp_extractor = WindsurfMCPConfigExtractorFactory.create(self.system)
            
            # Initialize Roo Code extractors (MCP only)
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

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root
            - rules: List of rule file dicts with metadata
        """
        try:
            return self._claude_rules_extractor.extract_all_claude_rules()
        except Exception as e:
            logger.error(f"Error extracting Claude rules: {e}", exc_info=True)
            return []

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

    def _is_project_empty(self, project: Dict) -> bool:
        """Check if a project has no meaningful data (empty mcpServers and rules)."""
        mcp_servers = project.get("mcpServers", [])
        rules = project.get("rules", [])
        return len(mcp_servers) == 0 and len(rules) == 0

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
            if project_path.startswith(user_home_str):
                filtered_projects.append(project)

        filtered_tool = tool.copy()
        filtered_tool['projects'] = filtered_projects
        
        if 'permissions' in filtered_tool:
            perms = filtered_tool['permissions']
            settings_path = perms.get('settings_path', '')
            if settings_path and not settings_path.startswith(user_home_str):
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
        
        # Process tools using helper methods to reduce duplication
        if tool_name == "cursor":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._cursor_rules_extractor,
                self._cursor_mcp_extractor,
                self.extract_all_cursor_rules
            )
        
        elif tool_name == "claude code":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._claude_rules_extractor,
                self._claude_mcp_extractor,
                self.extract_all_claude_rules,
                merge_mcp_func=self._merge_claude_mcp_configs_into_projects
            )
            
            # Extract settings
            logger.info(f"  Extracting {tool_name} settings...")
            if self._claude_settings_extractor:
                try:
                    settings = self._claude_settings_extractor.extract_settings()
                    logger.info(f"  Settings extraction returned: {settings is not None}, count: {len(settings) if settings else 0}")
                    if settings:
                        num_settings = len(settings)
                        logger.info(f"  ✓ Found {num_settings} settings file(s)")
                        # Store settings to be included in tool dict
                        tool["_settings"] = settings
                        logger.info(f"  ✓ Stored _settings in tool dict (keys: {list(tool.keys())})")
                        # Log settings details
                        log_settings_details(settings, tool_name)
                    else:
                        logger.warning("  ⚠ No settings found - extract_settings() returned None or empty list")
                except Exception as e:
                    logger.error(f"Error extracting {tool_name} settings: {e}", exc_info=True)
            else:
                logger.warning(f"  ⚠ {tool_name} settings extractor not available for this OS")
        
        elif tool_name == "windsurf":
            projects_dict = self._process_tool_with_rules_and_mcp(
                tool,
                self._windsurf_rules_extractor,
                self._windsurf_mcp_extractor,
                self.extract_all_windsurf_rules
            )
        
        elif tool_name == "roo code":
            projects_dict = self._process_tool_with_mcp_only(
                tool,
                self._roo_mcp_extractor
            )
        
        elif tool_name == "cline":
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
        
        # Transform and add permissions if present (for Claude Code)
        logger.info(f"  Checking for settings in tool dict for {tool_name}...")
        logger.info(f"  Tool dict keys: {list(tool.keys())}")
        
        if "_settings" in tool:
            logger.info(f"  ✓ Found _settings in tool dict, count: {len(tool['_settings']) if tool['_settings'] else 0}")
            try:
                permissions = transform_settings_to_backend_format(tool["_settings"])
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
            logger.warning(f"  ✗ No _settings found in tool dict for {tool_name}")
            logger.warning(f"  Available keys in tool: {list(tool.keys())}")
        
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
        return {
            "home_user": home_user,
            "system_user": system_user or home_user,
            "device_id": device_id,
            "tools": [tool]
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
    
    # Checking for API key and domain
    if not args.api_key or not args.domain:
        print("Error: --api-key and --domain arguments are required")
        print("Please provide an API key and domain: python ai_tools_discovery.py --api-key YOUR_API_KEY --domain YOUR_DOMAIN")
        sys.exit(1)
    
    try:
        detector = AIToolsDetector()
        
        # Get device ID once (shared across all user reports)
        device_id = detector.get_device_id()
        
        # Get all users for macOS, or use current user for other platforms
        all_users = get_all_users_macos() if platform.system() == "Darwin" else []
        
        # If no users found or not macOS, fall back to current user behavior
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
        
        # Detect all unique tools across all users first (to know which tools to process)
        logger.info("Detecting AI tools...")
        all_tools = []  # Store all unique tools across all users
        tools_by_user = {}  # Track which tools belong to which user
        
        for user in all_users:
            user_home = Path(f"/Users/{user}") if platform.system() == "Darwin" else Path.home()
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
        
        # Use the aggregated tools list
        tools = all_tools
        logger.info(f"Detection complete: {len(tools)} unique tool(s) found across all users")
        logger.info("")
        
        # Process each tool, then explore all users for that tool and send reports
        for tool in tools:
            tool_name = tool.get('name', 'Unknown')
            
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
                    user_home = Path(f"/Users/{user_name}") if platform.system() == "Darwin" else Path.home()
                    
                    try:
                        # Filter projects to only include this user's projects
                        tool_filtered = detector.filter_tool_projects_by_user(tool_with_projects, user_home)
                        
                        # Skip if no projects for this user
                        if not tool_filtered.get('projects'):
                            logger.debug(f"    User {user_name}: No projects found for {tool_name}, skipping")
                            continue
                        
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
                        
                        tool_version = tool_filtered.get('version', 'Unknown version')
                        tool_path = tool_filtered.get('install_path', 'Unknown path')
                        
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
                            logger.info(f"  │   Source: {perms.get('settings_source', 'unknown')}")
                            logger.info(f"  │   Path: {perms.get('settings_path', 'unknown')}")
                            logger.info(f"  │   Permission Mode: {perms.get('permission_mode', 'not set')}")
                            logger.info(f"  │   Allow Rules: {len(perms.get('allow_rules', []))}")
                            logger.info(f"  │   Deny Rules: {len(perms.get('deny_rules', []))}")
                            logger.info(f"  │   Sandbox Enabled: {perms.get('sandbox_enabled', 'not set')}")
                        else:
                            logger.info(f"  │ Permissions: ✗ Not present")
                        
                        logger.info("  └──────────────────────────────────────────────────────────────────")
                        logger.info("")
                        
                        # Log the complete JSON being sent to backend
                        logger.info("  Complete JSON payload being sent to backend:")
                        logger.info("  " + "=" * 70)
                        try:
                            import json
                            report_json = json.dumps(single_tool_report, indent=2)
                            # Split into lines and add indentation for readability
                            for line in report_json.split('\n'):
                                logger.info(f"  {line}")
                        except Exception as e:
                            logger.warning(f"  Could not serialize report to JSON for logging: {e}")
                            logger.info(f"  Report structure: {single_tool_report}")
                        logger.info("  " + "=" * 70)
                        logger.info("")
                        
                        # Send report to backend
                        logger.info(f"  Sending {tool_name} report for user {user_name} to backend...")
                        
                        if args.api_key and args.domain:
                            if send_report_to_backend(args.domain, args.api_key, single_tool_report, args.app_name):
                                logger.info(f"  ✓ {tool_name} report for user {user_name} sent successfully")
                            else:
                                logger.error(f"  ✗ Failed to send {tool_name} report for user {user_name} to backend")
                        else:
                            logger.warning(f"  ⚠ Skipping backend send - API key or domain not provided")
                        
                        logger.info("")
                        
                    except Exception as e:
                        logger.error(f"Error processing {tool_name} for user {user_name}: {e}", exc_info=True)
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
                logger.info("")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
