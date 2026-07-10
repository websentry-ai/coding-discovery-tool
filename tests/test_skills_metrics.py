"""
Per-flow skills metrics.

Each SKILL.md extraction flow records a counter — including a ZERO count. A
silently-failing extractor (vendor path change, permission regression) raises no
exception and fires no Sentry event; it only logs "No skills found", which is
indistinguishable from a machine that genuinely has none. The per-tool counter is
what makes that regression alertable across a fleet.
"""

import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector, _metric_safe_name


class TestMetricSafeName(unittest.TestCase):
    def test_spaces_become_underscores(self):
        # Sentry metric keys must match [a-zA-Z_][a-zA-Z0-9_.\-]*
        self.assertEqual(_metric_safe_name("Gemini CLI"), "gemini_cli")
        self.assertEqual(_metric_safe_name("Roo Code"), "roo_code")

    def test_simple_names_lowercased(self):
        self.assertEqual(_metric_safe_name("Codex"), "codex")
        self.assertEqual(_metric_safe_name("Windsurf"), "windsurf")

    def test_leading_non_letter_is_prefixed(self):
        self.assertTrue(_metric_safe_name("3tool").startswith("_"))

    def test_empty_is_unknown(self):
        self.assertEqual(_metric_safe_name(""), "unknown")
        self.assertEqual(_metric_safe_name(None), "unknown")


class TestSkillsMetricRecording(unittest.TestCase):
    def setUp(self):
        self.d = AIToolsDetector()
        self.d.skills_metrics = {}

    def _run(self, skills_result, extractor=object()):
        projects = {}
        self.d._extract_and_merge_tool_skills(
            "Gemini CLI", extractor, lambda: skills_result, projects
        )
        return projects

    def test_zero_result_is_still_recorded(self):
        # THE point of this metric: 0 must be emitted, not omitted.
        self._run({"user_skills": [], "project_skills": []})
        m = self.d.skills_metrics["gemini_cli"]
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["user_skills"], 0)
        self.assertEqual(m["project_skills"], 0)
        self.assertEqual(m["projects"], 0)

    def test_counts_recorded(self):
        self._run({
            "user_skills": [{"skill_name": "a", "project_path": "/h"}],
            "project_skills": [
                {"project_root": "/p1", "skills": [{"skill_name": "x"}, {"skill_name": "y"}]},
                {"project_root": "/p2", "skills": [{"skill_name": "z"}]},
            ],
        })
        m = self.d.skills_metrics["gemini_cli"]
        self.assertEqual(m["user_skills"], 1)
        self.assertEqual(m["project_skills"], 3)   # 2 + 1
        self.assertEqual(m["projects"], 2)
        self.assertEqual(m["status"], "ok")

    def test_unsupported_os_recorded(self):
        self.d._extract_and_merge_tool_skills("Gemini CLI", None, lambda: None, {})
        self.assertEqual(self.d.skills_metrics["gemini_cli"]["status"], "unsupported_os")

    def test_broken_extractor_via_real_wrapper_records_error(self):
        # PRODUCTION path: extract_all_<tool>_skills catches the inner exception and
        # returns None (it does NOT re-raise). The merge helper must still record
        # status="error" — a None result means the extractor failed, not that the
        # machine has zero skills. Uses the REAL wrapper so the double try/except
        # interaction is exercised, not bypassed.
        class _Boom:
            def extract_all_skills(self):
                raise RuntimeError("extractor blew up")

        self.d._gemini_cli_skills_extractor = _Boom()
        with patch("scripts.coding_discovery_tools.ai_tools_discovery.report_to_sentry"):
            self.d._extract_and_merge_tool_skills(
                "Gemini CLI",
                self.d._gemini_cli_skills_extractor,
                self.d.extract_all_gemini_cli_skills,   # the real wrapper (swallows -> None)
                {},
            )
        self.assertEqual(self.d.skills_metrics["gemini_cli"]["status"], "error")

    def test_none_result_recorded_as_error(self):
        # A None result (however produced) is a failure, never a genuine zero.
        self._run(None)
        self.assertEqual(self.d.skills_metrics["gemini_cli"]["status"], "error")

    def test_direct_extract_func_exception_still_error(self):
        # Defensive: if extract_skills_func itself raises (doesn't swallow), the
        # helper's own except records error too.
        def boom():
            raise RuntimeError("extractor blew up")

        with patch("scripts.coding_discovery_tools.ai_tools_discovery.report_to_sentry"):
            self.d._extract_and_merge_tool_skills("Gemini CLI", object(), boom, {})
        self.assertEqual(self.d.skills_metrics["gemini_cli"]["status"], "error")

    def test_recording_never_raises(self):
        # Telemetry bookkeeping must never break a scan.
        with patch.object(self.d, "skills_metrics", None):
            self.d._record_skills_metric("Codex", status="ok")  # would TypeError internally


if __name__ == "__main__":
    unittest.main()
