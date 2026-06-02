"""
MCP config extraction for the GitHub Copilot CLI on Windows systems.

DRY decision (CLAUDE.md): the Copilot CLI MCP extraction is 100% OS-agnostic —
the config path is identical (``~/.copilot/mcp-config.json``, i.e.
``%USERPROFILE%\\.copilot``), the all-users scan is handled by the shared
``extract_ide_global_configs_with_root_support`` (which already branches on
``platform.system()`` for the Windows admin + ``C:\\Users`` case), and the
parser (JSONC comments + trailing commas + wrapped/flat server resolution) is
pure string/JSON logic. So this Windows extractor is a thin subclass of the
macOS one with NO duplicated parsing — the trailing-comma fix (review P1) and
every future parser change are shared automatically. A distinct ``Windows``
class is kept (rather than reusing the macOS class directly in the factory) only
so the ``CopilotCliMCPConfigExtractorFactory`` stays symmetric with every sibling
factory, which returns a per-OS ``WindowsXxx`` type.
"""

from ...macos.copilot_cli.mcp_config_extractor import (
    MacOSCopilotCliMCPConfigExtractor,
)


class WindowsCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """GitHub Copilot CLI MCP config extractor on Windows.

    Identical behaviour to the macOS extractor: the logic is OS-agnostic (see the
    module docstring). This subclass exists purely to keep the per-OS factory
    contract symmetric; it deliberately adds no parser code.
    """
    pass
