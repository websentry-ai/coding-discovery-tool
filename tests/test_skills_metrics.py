"""
Per-flow skills metrics.

Each SKILL.md extraction flow records a counter — including a ZERO count. A
silently-failing extractor (vendor path change, permission regression) raises no
exception and fires no Sentry event; it only logs "No skills found", which is
indistinguishable from a machine that genuinely has none. The per-tool counter is
what makes that regression alertable across a fleet.
"""

import unittest
from pathlib import Path
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

    def test_user_skill_without_home_dropped_not_attributed_to_scanner(self):
        # A user skill missing project_path must NOT fall back to Path.home()
        # (== the scanner's home, e.g. /root, under a privileged scan) — it must be
        # dropped and counted, never mis-filed into the wrong user's report.
        projects = {}
        self.d._extract_and_merge_tool_skills(
            "Gemini CLI", object(),
            lambda: {
                "user_skills": [
                    {"skill_name": "orphan", "project_path": None},        # no home -> dropped
                    {"skill_name": "ok", "project_path": "/Users/alice"},  # kept
                ],
                "project_skills": [],
            },
            projects,
        )
        # dropped one is NOT bucketed under Path.home()
        self.assertNotIn(str(Path.home()), projects)
        self.assertIn("/Users/alice", projects)
        self.assertEqual([s["skill_name"] for s in projects["/Users/alice"]["skills"]], ["ok"])
        m = self.d.skills_metrics["gemini_cli"]
        self.assertEqual(m["user_skills"], 1)      # only the attributable one counted
        self.assertEqual(m["dropped_no_home"], 1)

    def test_legacy_inline_tool_recorded(self):
        # Legacy tools (Claude Code / Cursor / Cline / Augment / Copilot CLI / Cowork)
        # merge skills inline; _record_skills_result_metric puts them in the payload
        # too, so drop alerting has no blind spot.
        self.d._record_skills_result_metric("Claude Code", {
            "user_skills": [{"skill_name": "a"}, {"skill_name": "b"}],
            "project_skills": [{"project_root": "/p", "skills": [{"skill_name": "x"}]}],
        })
        m = self.d.skills_metrics["claude_code"]
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["user_skills"], 2)
        self.assertEqual(m["project_skills"], 1)
        self.assertEqual(m["projects"], 1)

    def test_legacy_inline_tool_none_recorded_as_error(self):
        # A crashed inline extractor returns None. Every legacy call site sits inside
        # `if self._<tool>_skills_extractor:` (unsupported-OS already excluded) and the
        # wrappers return None ONLY on exception -- a genuine "no skills" returns a dict
        # with empty lists. So None means FAILED and must be status=error, exactly like
        # the newer tools. Recording it as a legitimate zero would hide a broken
        # extractor behind a plausible-looking count.
        self.d._record_skills_result_metric("Cline", None)
        self.assertEqual(self.d.skills_metrics["cline"]["status"], "error")

    def test_legacy_inline_tool_genuine_zero_is_ok_not_error(self):
        # The other side of the same coin: a real empty result is status=ok with zero
        # counts -- a machine with no skills must not look like a broken extractor.
        self.d._record_skills_result_metric("Cline", {"user_skills": [], "project_skills": []})
        m = self.d.skills_metrics["cline"]
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["user_skills"], 0)
        self.assertEqual(m["project_skills"], 0)
        self.assertEqual(m["projects"], 0)

    def test_recording_never_raises(self):
        # Telemetry bookkeeping must never break a scan.
        with patch.object(self.d, "skills_metrics", None):
            self.d._record_skills_metric("Codex", status="ok")  # would TypeError internally


if __name__ == "__main__":
    unittest.main()
