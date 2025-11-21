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
from typing import Dict, List, Optional

try:
    from .coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
    )
    from .utils import send_report_to_backend, get_user_info
except ImportError:
    # Running as script directly - add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scripts.coding_discovery_tools.coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
        CursorMCPConfigExtractorFactory,
        ClaudeMCPConfigExtractorFactory,
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
            self._device_id_extractor = DeviceIdExtractorFactory.create(self.system)
            self._tool_detectors = ToolDetectorFactory.create_all_tool_detectors(self.system)
            self._cursor_rules_extractor = CursorRulesExtractorFactory.create(self.system)
            self._claude_rules_extractor = ClaudeRulesExtractorFactory.create(self.system)
            self._cursor_mcp_extractor = CursorMCPConfigExtractorFactory.create(self.system)
            self._claude_mcp_extractor = ClaudeMCPConfigExtractorFactory.create(self.system)
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
        for mcp_project in mcp_projects:
            project_path = mcp_project["path"]
            mcp_servers = mcp_project.get("mcpServers", [])
            
            if project_path in projects_dict:
                # Merge MCP config into existing project
                projects_dict[project_path]["mcpServers"] = mcp_servers
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
        for mcp_project in mcp_projects:
            project_path = mcp_project["path"]
            mcp_servers = mcp_project.get("mcpServers", [])
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

    def generate_report(self) -> Dict:
        """
        Generate complete discovery report with tool detection and rules extraction.
        
        Returns:
            Dictionary with user info, device data, and tools (with nested projects)
        """
        device_id = self.get_device_id()
        user_info = get_user_info()
        tools = self.detect_all_tools()
        
        logger.info("Extracting Cursor rules...")
        cursor_projects = self.extract_all_cursor_rules()
        
        logger.info("Extracting Claude Code rules...")
        claude_projects = self.extract_all_claude_rules()

        logger.info("Extracting MCP configs...")
        cursor_mcp_config = self._cursor_mcp_extractor.extract_mcp_config()
        claude_mcp_config = self._claude_mcp_extractor.extract_mcp_config()

        # Transform projects: change project_root to path and prepare for merging
        cursor_projects_dict = {
            project["project_root"]: {
                "path": project["project_root"],
                "rules": project.get("rules", [])  # Ensure rules is always an array
            }
            for project in cursor_projects
        }
        
        claude_projects_dict = {
            project["project_root"]: {
                "path": project["project_root"],
                "rules": project.get("rules", [])  # Ensure rules is always an array
            }
            for project in claude_projects
        }

        # Merge MCP configs into projects
        if cursor_mcp_config and "projects" in cursor_mcp_config:
            self._merge_mcp_configs_into_projects(
                cursor_mcp_config["projects"],
                cursor_projects_dict
            )
        
        if claude_mcp_config and "projects" in claude_mcp_config:
            self._merge_claude_mcp_configs_into_projects(
                claude_mcp_config["projects"],
                claude_projects_dict
            )

        # Group projects by tool and add to tools array
        # Filter out projects with both empty mcpServers and empty rules
        def is_project_empty(project: Dict) -> bool:
            """Check if a project has no meaningful data (empty mcpServers and rules)."""
            mcp_servers = project.get("mcpServers", [])
            rules = project.get("rules", [])
            return len(mcp_servers) == 0 and len(rules) == 0
        
        tools_with_projects = []
        for tool in tools:
            tool_name = tool.get("name", "").lower()
            projects = []
            
            if tool_name == "cursor":
                projects = list(cursor_projects_dict.values())
            elif tool_name == "claude code":
                projects = list(claude_projects_dict.values())
            
            # Filter out empty projects (no mcpServers and no rules)
            filtered_projects = [project for project in projects if not is_project_empty(project)]
            
            tool_with_projects = {
                "name": tool.get("name"),
                "version": tool.get("version"),
                "install_path": tool.get("install_path"),
                "projects": filtered_projects
            }
            tools_with_projects.append(tool_with_projects)

        # Build report with user and device data separated
        # Keep device_id for backward compatibility
        return {
            "system_user": user_info,
            "device_id": device_id,  # Backward compatibility
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
        report = detector.generate_report()

        # Print summary
        num_tools = len(report['tools'])
        total_projects = 0
        total_rules = 0
        
        logger.info("=" * 60)
        logger.info("AI Tools Discovery Report")
        logger.info("=" * 60)
        logger.info(f"System User: {report.get('system_user', 'unknown')}")
        logger.info(f"Device ID: {report['device_id']}")
        logger.info("")
        logger.info(f"Tools Detected: {num_tools}")
        for tool in report['tools']:
            tool_name = tool.get('name', 'Unknown')
            tool_version = tool.get('version', 'Unknown version')
            tool_path = tool.get('install_path', 'Unknown path')
            projects = tool.get('projects', [])
            num_projects = len(projects)
            num_rules = sum(len(p.get('rules', [])) for p in projects)
            total_projects += num_projects
            total_rules += num_rules
            
            logger.info(f"  - {tool_name}: {tool_version} at {tool_path}")
            logger.info(f"    Projects: {num_projects}, Rules: {num_rules}")
        logger.info("")
        logger.info(f"Total: {total_projects} projects, {total_rules} rule files")
        logger.info("")
        logger.info("Full Report (JSON):")
        logger.info(json.dumps(report, indent=2))
        logger.info("=" * 60)
        
        # Send report to backend
        logger.info("")
        print("Sending report to backend...")
        if send_report_to_backend(args.domain, args.api_key, report):
            print("Report sent successfully")
        else:
            print("Failed to send report to backend")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
