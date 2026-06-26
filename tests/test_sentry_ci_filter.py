"""
Tests for the CI / local-run noise filter in report_to_sentry().

The discovery CI suite runs the real script against a loopback mock gateway on
clean GitHub-hosted runners; without this filter every such run floods the
*production* Sentry project with zero-impact events (DISCOVERY-TOOL-SCRIPT-17 /
-13 / -12 / -D). report_to_sentry() must drop an event when it carries a CI
fingerprint (a CI env marker, a loopback `domain`, or a `runner`/`runneradmin`
`system_user`) and must still send genuine customer events through.

No real network: the curl call (subprocess.run) is mocked, so "sent" is asserted
purely by whether the transport was invoked.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import (
    _event_domain_is_loopback,
    _is_ci_or_local_event,
    report_to_sentry,
    reset_sentry_run_state,
)

# A syntactically valid DSN so report_to_sentry() proceeds past the DSN check and
# reaches the transport (which is mocked) for the "passes through" cases.
_VALID_DSN = "https://abc123@o123.ingest.us.sentry.io/4510874666663936"
_REAL_DOMAIN = "https://api.getunbound.ai"


class _NoCiEnv(unittest.TestCase):
    """Base that neutralizes ambient CI env markers so the domain/user logic is
    exercised in isolation -- this suite itself runs under GitHub Actions, where
    GITHUB_ACTIONS=true would otherwise make _running_in_ci() drop everything."""

    def setUp(self):
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("GITHUB_ACTIONS", None)
        os.environ.pop("CI", None)

    def tearDown(self):
        self._env.stop()


class TestDomainLoopbackParsing(unittest.TestCase):
    """Unit tests for the host-extraction helper (env-independent)."""

    def test_loopback_variants_match(self):
        for domain in (
            "http://127.0.0.1:57412",
            "http://127.0.0.1",
            "127.0.0.1:8000",
            "https://127.255.255.255",
            "http://localhost:3001",
            "localhost",
            "http://[::1]:9000",
            "::1",  # bare IPv6 loopback
            "http://0.0.0.0:8080",
            "HTTP://LOCALHOST:8000",
            "http://user:pass@127.0.0.1:9000",
        ):
            with self.subTest(domain=domain):
                self.assertTrue(_event_domain_is_loopback(domain))

    def test_real_domains_do_not_match(self):
        for domain in (
            _REAL_DOMAIN,
            "https://tenant.getunbound.ai/api/v1/ai-tools/report/",
            "http://localhost.evil.com",       # not the loopback host
            "https://127.example.com",          # FQDN beginning "127." is not loopback
            "http://127.0.0.1.evil.com:8000",   # loopback-looking prefix, real FQDN
            "https://127-fake.example.com",
            "",
        ):
            with self.subTest(domain=domain):
                self.assertFalse(_event_domain_is_loopback(domain))


class TestIsCiOrLocalEvent(_NoCiEnv):
    """Unit tests for the combined CI/local predicate."""

    def test_loopback_domain_is_ci(self):
        self.assertTrue(_is_ci_or_local_event({"domain": "http://127.0.0.1:57412"}))

    def test_runner_users_are_ci(self):
        self.assertTrue(_is_ci_or_local_event({"system_user": "runner", "domain": _REAL_DOMAIN}))
        self.assertTrue(_is_ci_or_local_event({"system_user": "runneradmin", "domain": _REAL_DOMAIN}))
        self.assertTrue(_is_ci_or_local_event({"system_user": "RunnerAdmin", "domain": _REAL_DOMAIN}))

    def test_ci_env_marker_is_ci_even_with_bare_context(self):
        # Covers the extract/detect-phase emits that carry neither domain nor user.
        os.environ["GITHUB_ACTIONS"] = "true"
        self.assertTrue(_is_ci_or_local_event({"phase": "extract", "tool_name": "X"}))

    def test_generic_ci_env_marker_is_ci(self):
        os.environ["CI"] = "true"
        self.assertTrue(_is_ci_or_local_event({}))

    def test_real_event_is_not_ci(self):
        self.assertFalse(_is_ci_or_local_event({"domain": _REAL_DOMAIN, "system_user": "jane"}))

    def test_empty_or_missing_keys_is_not_ci(self):
        self.assertFalse(_is_ci_or_local_event({}))
        self.assertFalse(_is_ci_or_local_event({"phase": "detect"}))

    def test_non_string_values_do_not_crash(self):
        # Defensive: a malformed context must never raise out of the predicate.
        self.assertFalse(_is_ci_or_local_event({"domain": 12345, "system_user": None}))


class TestReportToSentryFiltering(_NoCiEnv):
    """report_to_sentry() drops CI events and sends real ones (transport mocked)."""

    def setUp(self):
        super().setUp()
        reset_sentry_run_state()

    def _run(self, mock_run, context):
        # subprocess.run is mocked to a successful curl so nothing hits the network.
        mock_run.return_value = MagicMock(returncode=0, stdout="200")
        with patch.object(utils_mod, "_SENTRY_DSN", _VALID_DSN):
            report_to_sentry(RuntimeError("boom"), context=context, level="warning")

    @patch.object(utils_mod.subprocess, "run")
    def test_loopback_domain_dropped(self, mock_run):
        self._run(mock_run, {"domain": "http://127.0.0.1:57412", "phase": "no_tools_found"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_localhost_domain_dropped(self, mock_run):
        self._run(mock_run, {"domain": "http://localhost:8000", "phase": "send_report"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_ipv6_loopback_dropped(self, mock_run):
        self._run(mock_run, {"domain": "http://[::1]:9000", "phase": "abort"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_runner_user_dropped_even_with_real_domain(self, mock_run):
        self._run(mock_run, {"domain": _REAL_DOMAIN, "system_user": "runner"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_runneradmin_user_dropped(self, mock_run):
        self._run(mock_run, {"domain": _REAL_DOMAIN, "system_user": "runneradmin"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_ci_env_marker_dropped_with_bare_context(self, mock_run):
        os.environ["GITHUB_ACTIONS"] = "true"
        self._run(mock_run, {"phase": "extract", "tool_name": "Claude rules"})
        mock_run.assert_not_called()

    @patch.object(utils_mod.subprocess, "run")
    def test_real_event_sent(self, mock_run):
        self._run(mock_run, {"domain": _REAL_DOMAIN, "system_user": "jane", "phase": "send_report"})
        mock_run.assert_called_once()

    @patch.object(utils_mod.subprocess, "run")
    def test_no_context_sent(self, mock_run):
        self._run(mock_run, None)
        mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
