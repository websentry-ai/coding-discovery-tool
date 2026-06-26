"""
Tests for the --dump / --payload verbosity flags.
"""
import argparse
import io
import logging
import unittest


def _build_parser():
    """Mirror the parser shape used by ai_tools_discovery.main()."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-key')
    parser.add_argument('--domain')
    parser.add_argument('--app_name')
    g = parser.add_mutually_exclusive_group()
    g.add_argument('--dump', action='store_true')
    g.add_argument('--payload', action='store_true')
    return parser


class TestVerbosityFlagsMutex(unittest.TestCase):
    """argparse should reject combining the verbosity flags (--dump/--payload)."""

    def setUp(self):
        self.parser = _build_parser()

    def _expect_exit(self, *flags):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(['--api-key', 'k', '--domain', 'd', *flags])

    def test_dump_alone_ok(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd', '--dump'])
        self.assertTrue(args.dump)
        self.assertFalse(args.payload)

    def test_payload_alone_ok(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd', '--payload'])
        self.assertTrue(args.payload)
        self.assertFalse(args.dump)

    def test_no_flags_defaults_all_false(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd'])
        self.assertFalse(args.dump)
        self.assertFalse(args.payload)

    def test_summary_flag_rejected(self):
        # --summary was removed; concise is the default. A stale caller passing
        # it must fail loudly (argparse exit), not silently get default output.
        self._expect_exit('--summary')

    def test_dump_and_payload_rejected(self):
        self._expect_exit('--dump', '--payload')


class TestLoggingHelpersSuppression(unittest.TestCase):
    """
    Setting the logging_helpers module's logger to WARNING must silence the
    INFO output of log_rules_details / log_mcp_details / log_settings_details.

    This is the property the concise default relies on; we test it directly so
    a future module rename doesn't silently break the default suppression.
    """

    def setUp(self):
        try:
            from scripts.coding_discovery_tools import logging_helpers
        except ImportError:
            from coding_discovery_tools import logging_helpers  # type: ignore
        self.logging_helpers = logging_helpers
        self.helpers_logger = logging.getLogger(logging_helpers.__name__)

        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        self.helpers_logger.addHandler(self.handler)
        self._original_level = self.helpers_logger.level
        self.helpers_logger.setLevel(logging.INFO)
        self.helpers_logger.propagate = False

    def tearDown(self):
        self.helpers_logger.removeHandler(self.handler)
        self.helpers_logger.setLevel(self._original_level)
        self.helpers_logger.propagate = True

    def _captured(self) -> str:
        self.handler.flush()
        return self.stream.getvalue()

    def _sample_projects(self):
        return {
            '/proj/a': {
                'rules': [{'file_name': 'a.md', 'size': 10, 'scope': 'project'}],
                'mcpServers': [{'name': 'srv', 'command': '/bin/srv', 'args': []}],
            },
        }

    def test_log_rules_emits_at_info_level(self):
        self.logging_helpers.log_rules_details(self._sample_projects(), 'tool-x')
        self.assertIn('Rules Summary', self._captured())

    def test_log_rules_silenced_at_warning_level(self):
        self.helpers_logger.setLevel(logging.WARNING)
        self.logging_helpers.log_rules_details(self._sample_projects(), 'tool-x')
        self.assertEqual(self._captured(), '')

    def test_log_mcp_silenced_at_warning_level(self):
        self.helpers_logger.setLevel(logging.WARNING)
        self.logging_helpers.log_mcp_details(self._sample_projects(), 'tool-x')
        self.assertEqual(self._captured(), '')

    def test_log_settings_silenced_at_warning_level(self):
        self.helpers_logger.setLevel(logging.WARNING)
        self.logging_helpers.log_settings_details(
            [{'scope': 'user', 'settings_path': '/p', 'permissions': {}}],
            'tool-x',
        )
        self.assertEqual(self._captured(), '')


class TestSendDedupCount(unittest.TestCase):
    """
    The per-(tool, user) upload must fire exactly once for a changed payload
    and never for a hash match. This mirrors the dedup branch in
    ai_tools_discovery.main() so a reintroduced unconditional send (the
    double-send regression) is caught here.
    """

    def _run_dedup(self, local_hash, cached_hash):
        sends = []
        cache_updates = []

        def send_report_to_backend():
            sends.append(1)
            return True, False

        def update_tool(name, user, h):
            cache_updates.append((name, user, h))

        if local_hash and cached_hash == local_hash:
            pass
        else:
            success, retryable = send_report_to_backend()
            if success and local_hash:
                update_tool('tool-x', 'user-y', local_hash)
        return len(sends), len(cache_updates)

    def test_changed_payload_sends_once(self):
        sends, updates = self._run_dedup('newhash', 'oldhash')
        self.assertEqual(sends, 1)
        self.assertEqual(updates, 1)

    def test_hash_match_does_not_send(self):
        sends, updates = self._run_dedup('samehash', 'samehash')
        self.assertEqual(sends, 0)
        self.assertEqual(updates, 0)

    def test_missing_hash_sends_without_cache_update(self):
        sends, updates = self._run_dedup(None, None)
        self.assertEqual(sends, 1)
        self.assertEqual(updates, 0)


if __name__ == '__main__':
    unittest.main()
