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
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
    )
    from .utils import send_report_to_backend, get_user_info
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
        WindsurfMCPConfigExtractorFactory,
        RooMCPConfigExtractorFactory,
        ClineMCPConfigExtractorFactory,
        AntigravityMCPConfigExtractorFactory,
        KiloCodeMCPConfigExtractorFactory,
        GeminiCliMCPConfigExtractorFactory,
        CodexMCPConfigExtractorFactory,
        OpenCodeMCPConfigExtractorFactory,
    )
    from scripts.coding_discovery_tools.utils import send_report_to_backend, get_user_info

# Set up logger
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


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

    def detect_all_tools(self) -> List[Dict]:
        """
        Detect all supported AI tools.
        
        Returns:
            List of detected tools with their info
        """
        tools = []

        for detector in self._tool_detectors:
            try:
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
                    self._log_rules_details(projects_dict, tool_name)
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
                    self._log_mcp_details(projects_dict, tool_name)
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
                    self._log_mcp_details(projects_dict, tool_name)
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

    def _log_rules_details(self, projects_dict: Dict[str, Dict], tool_name: str) -> None:
        """
        Log detailed information about rules found.
        
        Args:
            projects_dict: Dictionary mapping project paths to project configs
            tool_name: Name of the tool
        """
        total_rules = 0
        projects_with_rules = []
        
        for project_path, project_data in projects_dict.items():
            rules = project_data.get("rules", [])
            if rules:
                total_rules += len(rules)
                projects_with_rules.append((project_path, rules))
        
        if total_rules == 0:
            logger.info("    No rules found")
            return
        
        logger.info("")
        logger.info("    ┌─ Rules Summary ─────────────────────────────────────────────")
        for idx, (project_path, rules) in enumerate(projects_with_rules, 1):
            logger.info(f"    │ Project #{idx}: {project_path}")
            logger.info(f"    │   Rules: {len(rules)}")
            for rule_idx, rule in enumerate(rules, 1):
                rule_file = rule.get("file_name") or rule.get("file_path", "Unknown")
                rule_size = rule.get("size", 0)
                size_str = f"{rule_size:,} bytes" if rule_size > 0 else "size unknown"
                logger.info(f"    │     {rule_idx}. {rule_file} ({size_str})")
            if idx < len(projects_with_rules):
                logger.info("    │")
        
        logger.info(f"    └─ Total: {total_rules} rule file(s) across {len(projects_with_rules)} project(s)")
        logger.info("")

    def _log_mcp_details(self, projects_dict: Dict[str, Dict], tool_name: str) -> None:
        """
        Log detailed information about MCP servers found.
        
        Args:
            projects_dict: Dictionary mapping project paths to project configs
            tool_name: Name of the tool
        """
        total_mcp_servers = 0
        projects_with_mcp = []
        
        for project_path, project_data in projects_dict.items():
            mcp_servers = project_data.get("mcpServers", [])
            if mcp_servers:
                total_mcp_servers += len(mcp_servers)
                projects_with_mcp.append((project_path, mcp_servers))
        
        if total_mcp_servers == 0:
            logger.info("    No MCP servers found")
            return
        
        logger.info("")
        logger.info("    ┌─ MCP Servers Summary ───────────────────────────────────────")
        for idx, (project_path, mcp_servers) in enumerate(projects_with_mcp, 1):
            logger.info(f"    │ Project #{idx}: {project_path}")
            logger.info(f"    │   MCP Servers: {len(mcp_servers)}")
            for server_idx, server in enumerate(mcp_servers, 1):
                server_name = server.get("name", "Unknown")
                server_command = server.get("command", "")
                server_args = server.get("args", [])
                
                logger.info(f"    │     {server_idx}. {server_name}")
                if server_command:
                    args_str = " ".join(str(arg) for arg in server_args) if server_args else ""
                    full_command = f"{server_command} {args_str}".strip()
                    logger.info(f"    │        Command: {full_command}")
                elif server_args:
                    logger.info(f"    │        Args: {' '.join(str(arg) for arg in server_args)}")
            if idx < len(projects_with_mcp):
                logger.info("    │")
        
        logger.info(f"    └─ Total: {total_mcp_servers} MCP server(s) across {len(projects_with_mcp)} project(s)")
        logger.info("")

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
        
        return {
            "name": tool.get("name"),
            "version": tool.get("version"),
            "install_path": tool.get("install_path"),
            "projects": filtered_projects
        }

    def generate_single_tool_report(self, tool: Dict, device_id: str, user_info: str) -> Dict:
        """
        Generate a report for a single tool with user and device info.
        
        Args:
            tool: Tool dict with projects populated
            device_id: Device identifier
            user_info: User information
            
        Returns:
            Report dictionary with single tool
        """
        return {
            "system_user": user_info,
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
    args = parser.parse_args()
    
    # Check for API key and domain
    if not args.api_key or not args.domain:
        print("Error: --api-key and --domain arguments are required")
        print("Please provide an API key and domain: python ai_tools_discovery.py --api-key YOUR_API_KEY --domain YOUR_DOMAIN")
        sys.exit(1)
    
    try:
        detector = AIToolsDetector()
        
        # Get device and user info once (shared across all tool reports)
        device_id = detector.get_device_id()
        user_info = get_user_info()
        
        # Detect all tools first
        logger.info("Detecting AI tools...")
        tools = detector.detect_all_tools()
        logger.info(f"Detection complete: {len(tools)} tool(s) found")
        if tools:
            for tool in tools:
                logger.info(f"  - {tool.get('name', 'Unknown')}: {tool.get('version', 'Unknown version')} at {tool.get('install_path', 'Unknown path')}")
        logger.info("")
        
        logger.info("=" * 60)
        logger.info("AI Tools Discovery Report")
        logger.info("=" * 60)
        logger.info(f"System User: {user_info}")
        logger.info(f"Device ID: {device_id}")
        logger.info("")
        logger.info(f"Tools Detected: {len(tools)}")
        logger.info("")
        
        # Process and send each tool immediately after processing
        total_projects = 0
        total_rules = 0
        all_tools_summary = []
        
        for tool in tools:
            tool_name = tool.get('name', 'Unknown')
            
            try:
                # Process the tool (extract rules and MCP configs)
                tool_with_projects = detector.process_single_tool(tool)
                
                # Generate report for this single tool
                single_tool_report = detector.generate_single_tool_report(
                    tool_with_projects, device_id, user_info
                )
                
                # Log tool summary
                projects = tool_with_projects.get('projects', [])
                num_projects = len(projects)
                num_rules = sum(len(p.get('rules', [])) for p in projects)
                num_mcp_servers = sum(len(p.get('mcpServers', [])) for p in projects)
                total_projects += num_projects
                total_rules += num_rules
                
                tool_version = tool_with_projects.get('version', 'Unknown version')
                tool_path = tool_with_projects.get('install_path', 'Unknown path')
                
                logger.info(f"  - {tool_name}: {tool_version} at {tool_path}")
                logger.info(f"    Projects: {num_projects}, Rules: {num_rules}, MCP Servers: {num_mcp_servers}")
                
                all_tools_summary.append({
                    'name': tool_name,
                    'version': tool_version,
                    'path': tool_path,
                    'projects': num_projects,
                    'rules': num_rules
                })
                
                # Send report immediately after processing
                logger.info(f"Sending {tool_name} report to backend...")
                if send_report_to_backend(args.domain, args.api_key, single_tool_report):
                    logger.info(f"{tool_name} report sent successfully")
                else:
                    logger.error(f"Failed to send {tool_name} report to backend")
                
                logger.info("")
                
            except Exception as e:
                logger.error(f"Error processing {tool_name}: {e}", exc_info=True)
                logger.info("")
        
        # Print final summary
        logger.info("=" * 60)
        logger.info("Summary")
        logger.info("=" * 60)
        for tool_summary in all_tools_summary:
            logger.info(f"  - {tool_summary['name']}: {tool_summary['projects']} projects, {tool_summary['rules']} rule files")
        logger.info(f"Total: {total_projects} projects, {total_rules} rule files")
        logger.info("=" * 60)
        logger.info("")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
