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

# Handle both direct execution and module import
try:
    from .coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
    )
    from .utils import verify_api_key, send_report_to_backend
except ImportError:
    # Running as script directly - add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scripts.coding_discovery_tools.coding_tool_factory import (
        DeviceIdExtractorFactory,
        ToolDetectorFactory,
        CursorRulesExtractorFactory,
        ClaudeRulesExtractorFactory,
    )
    from scripts.coding_discovery_tools.utils import verify_api_key, send_report_to_backend

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

    def generate_report(self) -> Dict:
        """
        Generate complete discovery report with tool detection and rules extraction.
        
        Returns:
            Dictionary with device_id, tools, cursor_rules, claude_rules, and timestamp
        """
        device_id = self.get_device_id()
        tools = self.detect_all_tools()
        
        logger.info("Extracting Cursor rules...")
        cursor_projects = self.extract_all_cursor_rules()
        
        logger.info("Extracting Claude Code rules...")
        claude_projects = self.extract_all_claude_rules()

        return {
            "device_id": device_id,
            "tools": tools,
            "cursor_rules": cursor_projects,
            "claude_rules": claude_projects,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description='AI Tools Discovery Script')
    parser.add_argument('--api-key', type=str, help='API key for authentication and report submission')
    args = parser.parse_args()
    
    # Check for API key
    if not args.api_key:
        print("Error: --api-key argument is required")
        print("Please provide an API key: python ai_tools_discovery.py --api-key YOUR_API_KEY")
        sys.exit(1)
    
    # Verify API key
    print("Verifying API key...")
    if not verify_api_key(args.api_key):
        print("Error: Invalid API key")
        sys.exit(1)
    print("API key verified successfully")
    
    try:
        detector = AIToolsDetector()
        report = detector.generate_report()

        # Print summary
        num_tools = len(report['tools'])
        num_cursor_projects = len(report['cursor_rules'])
        num_cursor_rules = sum(len(p['rules']) for p in report['cursor_rules'])
        num_claude_projects = len(report['claude_rules'])
        num_claude_rules = sum(len(p['rules']) for p in report['claude_rules'])
        
        logger.info("=" * 60)
        logger.info("AI Tools Discovery Report")
        logger.info("=" * 60)
        logger.info(f"Device ID: {report['device_id']}")
        logger.info(f"Timestamp: {report['timestamp']}")
        logger.info("")
        logger.info(f"Tools Detected: {num_tools}")
        for tool in report['tools']:
            logger.info(f"  - {tool.get('name', 'Unknown')}: {tool.get('version', 'Unknown version')} at {tool.get('install_path', 'Unknown path')}")
        logger.info("")
        logger.info(f"Cursor Rules: {num_cursor_projects} projects, {num_cursor_rules} rule files")
        logger.info(f"Claude Rules: {num_claude_projects} projects, {num_claude_rules} rule files")
        logger.info("")
        logger.info("Full Report (JSON):")
        logger.info(json.dumps(report, indent=2))
        logger.info("=" * 60)
        
        # Send report to backend
        logger.info("")
        print("Sending report to backend...")
        if send_report_to_backend(args.api_key, report):
            print("Report sent successfully")
        else:
            print("Failed to send report to backend")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
