"""Linux-specific implementations for AI tools discovery."""

from .device_id import LinuxDeviceIdExtractor
from .claude_code import LinuxClaudeDetector, LinuxClaudeSettingsExtractor, LinuxClaudeSkillsExtractor
from .cursor import LinuxCursorDetector, LinuxCursorSettingsExtractor, LinuxCursorSkillsExtractor
from .windsurf import LinuxWindsurfDetector, LinuxWindsurfSkillsExtractor
from .roo_code import LinuxRooDetector, LinuxRooRulesExtractor, LinuxRooMCPConfigExtractor, LinuxRooSkillsExtractor
from .cline import LinuxClineDetector, LinuxClineRulesExtractor, LinuxClineMCPConfigExtractor, LinuxClineSkillsExtractor
from .antigravity import LinuxAntigravityDetector, LinuxAntigravityRulesExtractor, LinuxAntigravityMCPConfigExtractor
from .kilocode import LinuxKiloCodeDetector, LinuxKiloCodeRulesExtractor, LinuxKiloCodeMCPConfigExtractor, LinuxKiloCodeSkillsExtractor
from .gemini_cli import LinuxGeminiCliDetector, LinuxGeminiCliRulesExtractor, LinuxGeminiCliMCPConfigExtractor, LinuxGeminiCliSkillsExtractor
from .cursor_cli import LinuxCursorCliDetector, LinuxCursorCliRulesExtractor, LinuxCursorCliMCPConfigExtractor, LinuxCursorCliSettingsExtractor
from .copilot_cli import LinuxCopilotCliDetector, LinuxCopilotCliMCPConfigExtractor, LinuxCopilotCliRulesExtractor, LinuxCopilotCliSettingsExtractor, LinuxCopilotCliSkillsExtractor
from .augment import LinuxAugmentDetector, LinuxAugmentMCPConfigExtractor, LinuxAugmentRulesExtractor, LinuxAugmentSettingsExtractor, LinuxAugmentSkillsExtractor
from .codex import LinuxCodexDetector, LinuxCodexRulesExtractor, LinuxCodexMCPConfigExtractor, LinuxCodexSkillsExtractor
from .opencode import LinuxOpenCodeDetector, LinuxOpenCodeRulesExtractor, LinuxOpenCodeMCPConfigExtractor, LinuxOpenCodeSkillsExtractor
from .openclaw import LinuxOpenClawDetector
from .replit import LinuxReplitDetector, LinuxReplitSkillsExtractor
from .jetbrains import LinuxJetBrainsDetector, LinuxJetBrainsMCPConfigExtractor
from .github_copilot import LinuxCopilotDetector, LinuxGitHubCopilotRulesExtractor, LinuxGitHubCopilotMCPConfigExtractor
from .claude_cowork import LinuxClaudeCoworkDetector, LinuxClaudeCoworkSkillsExtractor
from .junie import LinuxJunieDetector, LinuxJunieRulesExtractor, LinuxJunieMCPConfigExtractor, LinuxJunieSkillsExtractor

__all__ = [
    "LinuxDeviceIdExtractor",
    "LinuxClaudeDetector",
    "LinuxClaudeSettingsExtractor",
    "LinuxClaudeSkillsExtractor",
    "LinuxCursorDetector",
    "LinuxCursorSettingsExtractor",
    "LinuxCursorSkillsExtractor",
    "LinuxWindsurfDetector",
    "LinuxWindsurfSkillsExtractor",
    "LinuxRooDetector",
    "LinuxRooRulesExtractor",
    "LinuxRooMCPConfigExtractor",
    "LinuxRooSkillsExtractor",
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
    "LinuxKiloCodeSkillsExtractor",
    "LinuxGeminiCliDetector",
    "LinuxGeminiCliRulesExtractor",
    "LinuxGeminiCliMCPConfigExtractor",
    "LinuxGeminiCliSkillsExtractor",
    "LinuxCursorCliDetector",
    "LinuxCursorCliRulesExtractor",
    "LinuxCursorCliMCPConfigExtractor",
    "LinuxCursorCliSettingsExtractor",
    "LinuxCopilotCliDetector",
    "LinuxCopilotCliMCPConfigExtractor",
    "LinuxCopilotCliRulesExtractor",
    "LinuxCopilotCliSettingsExtractor",
    "LinuxCopilotCliSkillsExtractor",
    "LinuxAugmentDetector",
    "LinuxAugmentMCPConfigExtractor",
    "LinuxAugmentRulesExtractor",
    "LinuxAugmentSettingsExtractor",
    "LinuxAugmentSkillsExtractor",
    "LinuxCodexDetector",
    "LinuxCodexRulesExtractor",
    "LinuxCodexMCPConfigExtractor",
    "LinuxCodexSkillsExtractor",
    "LinuxOpenCodeDetector",
    "LinuxOpenCodeRulesExtractor",
    "LinuxOpenCodeMCPConfigExtractor",
    "LinuxOpenCodeSkillsExtractor",
    "LinuxOpenClawDetector",
    "LinuxReplitDetector",
    "LinuxReplitSkillsExtractor",
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
    "LinuxJunieSkillsExtractor",
]
