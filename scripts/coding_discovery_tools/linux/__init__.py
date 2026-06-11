"""Linux-specific implementations for AI tools discovery."""

from .device_id import LinuxDeviceIdExtractor
from .claude_code import LinuxClaudeDetector, LinuxClaudeSettingsExtractor, LinuxClaudeSkillsExtractor
from .cursor import LinuxCursorDetector, LinuxCursorSettingsExtractor, LinuxCursorSkillsExtractor
from .windsurf import LinuxWindsurfDetector
from .roo_code import LinuxRooDetector, LinuxRooRulesExtractor, LinuxRooMCPConfigExtractor
from .cline import LinuxClineDetector, LinuxClineRulesExtractor, LinuxClineMCPConfigExtractor, LinuxClineSkillsExtractor
from .antigravity import LinuxAntigravityDetector, LinuxAntigravityRulesExtractor, LinuxAntigravityMCPConfigExtractor
from .kilocode import LinuxKiloCodeDetector, LinuxKiloCodeRulesExtractor, LinuxKiloCodeMCPConfigExtractor
from .gemini_cli import LinuxGeminiCliDetector, LinuxGeminiCliRulesExtractor, LinuxGeminiCliMCPConfigExtractor
from .cursor_cli import LinuxCursorCliDetector, LinuxCursorCliRulesExtractor, LinuxCursorCliMCPConfigExtractor, LinuxCursorCliSettingsExtractor
from .copilot_cli import LinuxCopilotCliDetector, LinuxCopilotCliMCPConfigExtractor, LinuxCopilotCliRulesExtractor, LinuxCopilotCliSettingsExtractor, LinuxCopilotCliSkillsExtractor
from .codex import LinuxCodexDetector, LinuxCodexRulesExtractor, LinuxCodexMCPConfigExtractor
from .opencode import LinuxOpenCodeDetector, LinuxOpenCodeRulesExtractor, LinuxOpenCodeMCPConfigExtractor
from .openclaw import LinuxOpenClawDetector
from .replit import LinuxReplitDetector
from .jetbrains import LinuxJetBrainsDetector, LinuxJetBrainsMCPConfigExtractor
from .github_copilot import LinuxCopilotDetector, LinuxGitHubCopilotRulesExtractor, LinuxGitHubCopilotMCPConfigExtractor
from .claude_cowork import LinuxClaudeCoworkDetector, LinuxClaudeCoworkSkillsExtractor
from .junie import LinuxJunieDetector, LinuxJunieRulesExtractor, LinuxJunieMCPConfigExtractor

__all__ = [
    "LinuxDeviceIdExtractor",
    "LinuxClaudeDetector",
    "LinuxClaudeSettingsExtractor",
    "LinuxClaudeSkillsExtractor",
    "LinuxCursorDetector",
    "LinuxCursorSettingsExtractor",
    "LinuxCursorSkillsExtractor",
    "LinuxWindsurfDetector",
    "LinuxRooDetector",
    "LinuxRooRulesExtractor",
    "LinuxRooMCPConfigExtractor",
    "LinuxClineDetector",
    "LinuxClineRulesExtractor",
    "LinuxClineMCPConfigExtractor",
    "LinuxClineSkillsExtractor",
    "LinuxAntigravityDetector",
    "LinuxAntigravityRulesExtractor",
    "LinuxAntigravityMCPConfigExtractor",
    "LinuxKiloCodeDetector",
    "LinuxKiloCodeRulesExtractor",
    "LinuxKiloCodeMCPConfigExtractor",
    "LinuxGeminiCliDetector",
    "LinuxGeminiCliRulesExtractor",
    "LinuxGeminiCliMCPConfigExtractor",
    "LinuxCursorCliDetector",
    "LinuxCursorCliRulesExtractor",
    "LinuxCursorCliMCPConfigExtractor",
    "LinuxCursorCliSettingsExtractor",
    "LinuxCopilotCliDetector",
    "LinuxCopilotCliMCPConfigExtractor",
    "LinuxCopilotCliRulesExtractor",
    "LinuxCopilotCliSettingsExtractor",
    "LinuxCopilotCliSkillsExtractor",
    "LinuxCodexDetector",
    "LinuxCodexRulesExtractor",
    "LinuxCodexMCPConfigExtractor",
    "LinuxOpenCodeDetector",
    "LinuxOpenCodeRulesExtractor",
    "LinuxOpenCodeMCPConfigExtractor",
    "LinuxOpenClawDetector",
    "LinuxReplitDetector",
    "LinuxJetBrainsDetector",
    "LinuxJetBrainsMCPConfigExtractor",
    "LinuxCopilotDetector",
    "LinuxGitHubCopilotRulesExtractor",
    "LinuxGitHubCopilotMCPConfigExtractor",
    "LinuxClaudeCoworkDetector",
    "LinuxClaudeCoworkSkillsExtractor",
    "LinuxJunieDetector",
    "LinuxJunieRulesExtractor",
    "LinuxJunieMCPConfigExtractor",
]
