"""Backward-compat guard: a stale ``--discovery-key`` from an older MDM policy or
cron must be accepted and ignored, never rejected as an unrecognized argument.

This exercises the REAL entry point via subprocess (not a mirrored parser),
because the outage happened when the shipped parser dropped the flag while
in-process mirror tests still "passed". A mirror cannot catch that divergence;
running the actual module can.
"""
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = "scripts.coding_discovery_tools.ai_tools_discovery"

# argparse exits 2 on an unrecognized argument; the real auth gate exits 1.
ARGPARSE_ERROR_EXIT = 2
MISSING_AUTH_EXIT = 1


def _run(*args):
    env = dict(os.environ)
    env.pop("UNBOUND_API_KEY", None)  # force the missing-auth path, not a real scan
    return subprocess.run(
        [sys.executable, "-m", MODULE, *args],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120, env=env,
    )


class TestDeprecatedDiscoveryKeyTolerated(unittest.TestCase):
    def test_stale_discovery_key_is_not_rejected_as_unknown(self):
        # Deployed MDM policies/crons still pass --discovery-key. The run must
        # stop only at the real auth gate (missing --api-key), never at argparse
        # "unrecognized arguments".
        r = _run("--discovery-key", "stale-key-abc123")
        blob = (r.stdout + r.stderr).lower()
        self.assertNotIn("unrecognized arguments", blob, r.stderr)
        self.assertNotEqual(
            r.returncode, ARGPARSE_ERROR_EXIT,
            f"stale --discovery-key was rejected by argparse: {r.stderr}")
        self.assertIn("api-key", blob)  # stopped at the real gate, as expected

    def test_stale_discovery_key_does_not_satisfy_auth(self):
        # The flag is IGNORED, not treated as credentials: a domain plus the
        # stale key still requires a real --api-key.
        r = _run("--discovery-key", "stale-key-abc123",
                 "--domain", "https://example.invalid")
        self.assertEqual(
            r.returncode, MISSING_AUTH_EXIT,
            f"expected the missing-api-key gate: {r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
