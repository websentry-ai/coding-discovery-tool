import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from scripts.coding_discovery_tools.s3_uploader import compute_payload_hash
from scripts.coding_discovery_tools.windows.visual_studio.visual_studio import WindowsVisualStudioDetector


class TestDiscoveryAgentGroups(unittest.TestCase):
    def test_scanner_preserves_registry_groups_through_report_generation(self):
        scanner = AIToolsDetector(os_name="Darwin")
        reported_names = {
            "GitHub Copilot": "GitHub Copilot Chat (VS Code)",
            "GitHub Copilot CLI": "GitHub Copilot in a future terminal",
            "OpenCode": "Open Code",
            "Cline": "Cline (OpenCode)",
        }
        scanner._tool_detectors = [
            detector for detector in scanner._tool_detectors if detector.tool_name in reported_names
        ]
        expected = {
            "GitHub Copilot Chat (VS Code)": "github-copilot",
            "GitHub Copilot in a future terminal": "github-copilot",
            "Open Code": "opencode",
            "Cline (OpenCode)": "cline",
        }
        with ExitStack() as stack:
            for detector in scanner._tool_detectors:
                stack.enter_context(patch.object(detector, "detect", side_effect=lambda detector=detector: {
                    "name": reported_names[detector.tool_name], "version": "1", "install_path": "/fixture",
                }))
            stack.enter_context(patch.object(
                scanner, "_process_single_tool_raw", side_effect=lambda tool: {"name": tool["name"], "projects": []},
            ))
            stack.enter_context(patch.object(scanner, "get_device_id", return_value="fixture"))

            batch = scanner.generate_report()
            self.assertEqual({tool["name"]: tool["agent_group_id"] for tool in batch["tools"]}, expected)

            single_reports = [
                scanner.generate_single_tool_report(scanner.process_single_tool(tool), "fixture", "test-user")
                for tool in scanner.detect_all_tools()
            ]
            self.assertEqual({
                report["tools"][0]["name"]: report["tools"][0]["agent_group_id"]
                for report in single_reports
            }, expected)

            self.assertEqual(scanner.detect_tool("OpenCode")["agent_group_id"], "opencode")
            tool = single_reports[0]["tools"][0]
            old_tool = {key: value for key, value in tool.items() if key != "agent_group_id"}
            self.assertNotEqual(compute_payload_hash(tool), compute_payload_hash(old_tool))

    def test_visual_studio_keeps_the_host_and_its_copilot_component_separate(self):
        scanner = AIToolsDetector(os_name="Windows")
        scanner._tool_detectors = [
            detector for detector in scanner._tool_detectors if isinstance(detector, WindowsVisualStudioDetector)
        ]
        detector = scanner._tool_detectors[0]
        instance = {
            "installationVersion": "17.11.2",
            "installationPath": "fixture",
            "product": {"id": "Microsoft.VisualStudio.Product.Community"},
            "packages": [{"id": "Component.VisualStudio.GitHub.Copilot", "version": "1"}],
        }
        with (
            patch.object(detector, "_user_instance_dir", return_value=Path("fixture")),
            patch.object(detector, "_instance_states", return_value=([instance], False)),
        ):
            self.assertEqual(
                {tool["agent_group_id"] for tool in scanner.detect_all_tools()},
                {"visual-studio", "github-copilot"},
            )

    def test_jetbrains_inventory_uses_the_config_product_instead_of_display_labels(self):
        scanner = AIToolsDetector(os_name="Darwin")
        scanner._tool_detectors = [
            detector for detector in scanner._tool_detectors if isinstance(detector, MacOSJetBrainsDetector)
        ]
        detector = scanner._tool_detectors[0]
        inventory = [
            {
                "folder_name": folder_name,
                "display_name": label,
                "config_path": "fixture",
                "plan": "Unknown",
                "version": "2025.2",
            }
            for folder_name, label in [
                ("IntelliJIdea2025.2", "Renamed IntelliJ"),
                ("PyCharm2025.2", "Renamed PyCharm"),
            ]
        ]
        with (
            patch.object(detector, "_scan_for_ides", return_value=inventory),
            patch.object(detector, "_get_plugin_details", return_value=[]),
        ):
            self.assertEqual(
                {tool["agent_group_id"] for tool in scanner.detect_all_tools()},
                {"jetbrains-intellijidea", "jetbrains-pycharm"},
            )

    def test_all_supported_platforms_register_a_group_or_a_multi_product_detector(self):
        for platform in ("Darwin", "Windows", "Linux"):
            for detector in ToolDetectorFactory.create_all_tool_detectors(platform):
                with self.subTest(platform=platform, detector=detector.tool_name):
                    if detector.tool_name == "JetBrains IDEs":
                        self.assertIsNone(detector.agent_group_id)
                    else:
                        self.assertRegex(detector.agent_group_id, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
