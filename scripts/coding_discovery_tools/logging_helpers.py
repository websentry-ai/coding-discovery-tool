"""
Logging helpers for AI tools discovery.

Provides utilities for formatting and logging discovery results including
rules, MCP configs, and settings.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def configure_logger(level: int = logging.INFO, format_string: str = None) -> None:
    """
    Configure the root logger with standard settings.
    
    Args:
        level: Logging level (default: INFO)
        format_string: Custom format string. If None, uses default format.
    """
    if format_string is None:
        format_string = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=level,
        format=format_string
    )


def log_rules_details(projects_dict: Dict[str, Dict], tool_name: str) -> None:
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


def log_mcp_details(projects_dict: Dict[str, Dict], tool_name: str) -> None:
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


def log_settings_details(settings: List[Dict], tool_name: str) -> None:
    """
    Log detailed information about settings found.
    
    Args:
        settings: List of settings dicts
        tool_name: Name of the tool
    """
    if not settings:
        logger.info("    No settings found")
        return
    
    logger.info("")
    logger.info("    ┌─ Settings Summary ────────────────────────────────────────────")
    
    for idx, setting in enumerate(settings, 1):
        settings_source = setting.get("settings_source", "unknown")
        settings_path = setting.get("settings_path", "Unknown")
        permissions = setting.get("permissions", {})
        sandbox = setting.get("sandbox", {})
        
        logger.info(f"    │ Settings #{idx}: {settings_source.upper()}")
        logger.info(f"    │   Path: {settings_path}")
        
        # Log permissions
        default_mode = permissions.get("defaultMode")
        allow_list = permissions.get("allow", [])
        deny_list = permissions.get("deny", [])
        additional_dirs = permissions.get("additionalDirectories", [])
        
        if default_mode:
            logger.info(f"    │   Default Mode: {default_mode}")
        if allow_list:
            logger.info(f"    │   Allow Rules: {len(allow_list)}")
            for allow_idx, allow_rule in enumerate(allow_list[:5], 1):  # Show first 5
                logger.info(f"    │     {allow_idx}. {allow_rule}")
            if len(allow_list) > 5:
                logger.info(f"    │     ... and {len(allow_list) - 5} more")
        if deny_list:
            logger.info(f"    │   Deny Rules: {len(deny_list)}")
            for deny_idx, deny_rule in enumerate(deny_list[:5], 1):  # Show first 5
                logger.info(f"    │     {deny_idx}. {deny_rule}")
            if len(deny_list) > 5:
                logger.info(f"    │     ... and {len(deny_list) - 5} more")
        if additional_dirs:
            logger.info(f"    │   Additional Directories: {len(additional_dirs)}")
            for dir_idx, add_dir in enumerate(additional_dirs[:3], 1):  # Show first 3
                logger.info(f"    │     {dir_idx}. {add_dir}")
            if len(additional_dirs) > 3:
                logger.info(f"    │     ... and {len(additional_dirs) - 3} more")
        
        # Log sandbox_enabled only
        sandbox_enabled = sandbox.get("enabled")
        if sandbox_enabled is not None:
            logger.info(f"    │   Sandbox Enabled: {sandbox_enabled}")
        
        if idx < len(settings):
            logger.info("    │")
    
    logger.info(f"    └─ Total: {len(settings)} settings file(s)")
    logger.info("")

