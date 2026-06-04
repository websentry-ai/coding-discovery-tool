"""
MCP config extraction for the GitHub Copilot CLI on Linux.

The standalone Copilot CLI keeps its MCP servers in the user-scope file
``~/.copilot/mcp-config.json``. That path is handled by
``extract_ide_global_configs_with_root_support``, which is already Linux-aware
(it delegates to ``get_linux_user_homes()`` for the ``/root`` + ``/home/*``
scan). The Copilot CLI base does not currently walk for workspace-scope
``<project>/.mcp.json`` files, so there is nothing OS-specific left to override
here — the Linux extractor inherits the macOS base unchanged.

When workspace-scope ``.mcp.json`` discovery lands on the Copilot CLI base
(tracked in PR #155), the Linux seam overrides (so ``/home`` is not excluded via
the macOS ``SKIP_SYSTEM_DIRS``) should be added *there*, alongside the base
methods they override and with their own tests — not speculatively here.
"""

from ...macos.copilot_cli.mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor


class LinuxCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Extractor for GitHub Copilot CLI MCP config on Linux systems.

    The user-scope path (``~/.copilot/mcp-config.json``) is inherited unchanged —
    ``extract_ide_global_configs_with_root_support`` is already Linux-aware.
    There is no workspace-scope walk on the Copilot CLI base yet, so this
    subclass currently adds no OS-specific behavior; it exists for parity with
    the OS-per-class factory pattern.
    """
