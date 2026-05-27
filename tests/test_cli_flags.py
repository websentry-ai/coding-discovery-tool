"""
Tests for the --dump / --summary / --payload verbosity flags.
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
    g.add_argument('--summary', action='store_true')
    g.add_argument('--payload', action='store_true')
    return parser


class TestVerbosityFlagsMutex(unittest.TestCase):
    """argparse should reject any two-of-three verbosity flag combination."""

    def setUp(self):
        self.parser = _build_parser()

    def _expect_exit(self, *flags):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(['--api-key', 'k', '--domain', 'd', *flags])

    def test_dump_alone_ok(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd', '--dump'])
        self.assertTrue(args.dump)
        self.assertFalse(args.summary)
        self.assertFalse(args.payload)

    def test_summary_alone_ok(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd', '--summary'])
        self.assertTrue(args.summary)
        self.assertFalse(args.dump)
        self.assertFalse(args.payload)

    def test_payload_alone_ok(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd', '--payload'])
        self.assertTrue(args.payload)
        self.assertFalse(args.dump)
        self.assertFalse(args.summary)

    def test_no_flags_defaults_all_false(self):
        args = self.parser.parse_args(['--api-key', 'k', '--domain', 'd'])
        self.assertFalse(args.dump)
        self.assertFalse(args.summary)
        self.assertFalse(args.payload)

    def test_dump_and_summary_rejected(self):
        self._expect_exit('--dump', '--summary')

    def test_dump_and_payload_rejected(self):
        self._expect_exit('--dump', '--payload')

    def test_summary_and_payload_rejected(self):
        self._expect_exit('--summary', '--payload')

    def test_all_three_rejected(self):
        self._expect_exit('--dump', '--summary', '--payload')


class TestLoggingHelpersSuppression(unittest.TestCase):
    """
    Setting the logging_helpers module's logger to WARNING must silence the
    INFO output of log_rules_details / log_mcp_details / log_settings_details.

    This is the property --summary relies on; we test it directly so a future
    rename of the module doesn't silently break --summary.
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


if __name__ == '__main__':
    unittest.main()
