"""
MCP config extraction for the GitHub Copilot CLI on Linux.

The macOS extractor is fully OS-agnostic (it delegates to
``extract_ide_global_configs_with_root_support`` which already handles
``/root`` + ``/home/*`` on Linux). This thin subclass renames the class
for import clarity and wires in ``LinuxCopilotCliDetector``.
"""

from ...macos.copilot_cli.mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor


class LinuxCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Extractor for GitHub Copilot CLI MCP config on Linux systems."""
