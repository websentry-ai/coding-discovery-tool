"""The front doors, invoked for real.

A change to the discovery run is reachable through several entrypoints, and they
do not share a parser or even an import path: `install.sh` runs the tool as a
*module* (`-m scripts.coding_discovery_tools.ai_tools_discovery`), while the
suite elsewhere runs the same file by *path*. Those take different branches of
the dual import block at the top of the script, so one proves nothing about the
other. The connector sweep has an entrypoint of its own and is stdlib-only on
purpose, so that it can be run directly as a script.

Everything here invokes the shipped artifact as a subprocess against a local
backend. Nothing is simulated: a door that cannot be opened on this platform is
skipped out loud rather than reasoned about.

`install.sh` is deliberately absent. It clones the repository at `main` into a
temp directory and scans from there, so running it here would exercise whatever
is already released rather than this checkout -- it would pass on a branch whose
imports are broken, and fail when GitHub is unreachable. What it ends up calling
is the module form below, which these do run against this tree.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = "scripts.coding_discovery_tools.ai_tools_discovery"


class _Accepts(BaseHTTPRequestHandler):
    """Takes every report and says yes, recording what arrived."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            self.server.bodies.append(json.loads(raw))
        except ValueError:
            self.server.bodies.append({"unparsed": raw[:200].decode("utf-8", "replace")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"uuids": []}')

    def log_message(self, *_a):
        pass


class _RunsAFrontDoor(unittest.TestCase):
    """A local backend, an isolated queue and cache, and Sentry off.

    The queue does not live under HOME -- it is a per-UID file in /var/tmp -- so
    redirecting HOME would leave a run under test draining this developer's own
    backlog into the stub and then deleting it.
    """

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Accepts)
        self.server.bodies = []
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        self._dir = tempfile.mkdtemp(prefix="front-door-")
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self.queue_file = Path(self._dir) / "queue.json"
        self.home = Path(self._dir) / "home"
        self.home.mkdir(parents=True, exist_ok=True)

    def env(self, **extra):
        env = dict(os.environ)
        env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "AI_DISCOVERY_QUEUE_FILE": str(self.queue_file),
            "AI_DISCOVERY_SENTRY_DSN": "",
            "AI_DISCOVERY_BACKOFF_BASE_SECONDS": "0.01",
            "AI_DISCOVERY_BACKOFF_CAP_SECONDS": "0.05",
        })
        env.update(extra)
        return env

    def events(self):
        return [b.get("scan_event") for b in self.server.bodies if b.get("scan_event")]


class TheModuleFormIsTheOneInstallShRuns(_RunsAFrontDoor):
    """`install.sh` runs `python -m <module>`; the rest of the suite runs the same
    file by path. The script's import block branches on which of those it is, so
    a run that works one way can fail the other with an ImportError before any of
    this change is even reached.

    These assert that the door opens -- the module imports, argparse takes the
    flags, and the run reaches the reporting code -- not that a whole scan of the
    host finishes. A scan here walks every real user on the machine and takes as
    long as it takes; what a removed flag or a broken import costs is everything
    before that, and that is what is checked."""

    # Enough to get through the drain and the in_progress event, which is what
    # these assert. A full scan of the host is not the subject and costs minutes.
    SCAN_BUDGET = "30"

    def _run_module(self, **env):
        return subprocess.run(
            [sys.executable, "-m", MODULE,
             "--api-key", "test-key-000000", "--domain", self.url,
             "--timeout", self.SCAN_BUDGET],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=600, env=self.env(**env),
        )

    def test_the_module_form_reaches_the_reporting_code(self):
        out = self._run_module()

        self.assertNotIn(
            "unrecognized arguments", out.stderr,
            "argparse rejected the flags install.sh passes, so every device"
            " running this door scans nothing at all",
        )
        self.assertNotIn(
            "ModuleNotFoundError", out.stderr,
            "the module form takes a different import branch from the path form"
            f" and it does not resolve: {out.stderr[-600:]}",
        )
        self.assertIn(
            "in_progress", self.events(),
            "nothing arrived from the entrypoint install.sh actually uses:"
            f" {out.stderr[-600:]}",
        )

    def test_the_module_form_drains_a_queue_left_by_a_previous_run(self):
        """The drain is the change under test, it runs before detection, and the
        module form is how most devices reach it."""
        # Stamped from the clock: the loader drops anything older than a day, so
        # a literal here would quietly stop being drained at all.
        self.queue_file.write_text(json.dumps([
            {"report": {"device_id": "QUEUED-OLD", "home_user": "alice",
                        "tools": [{"name": "Claude Code"}]},
             "queued_at": datetime.now(timezone.utc).isoformat()},
        ]))

        out = self._run_module()

        self.assertIn(
            "QUEUED-OLD", [b.get("device_id") for b in self.server.bodies],
            f"the backlog was never drained through this door: {out.stderr[-600:]}",
        )
        self.assertFalse(
            self.queue_file.exists() and json.loads(self.queue_file.read_text()),
            "the delivered report was left on the queue, so the next run sends"
            " it again over whatever this one uploaded",
        )


class TheConnectorSweepStandsAlone(unittest.TestCase):
    """The sweep is changed by this work and has an entrypoint of its own. It is
    deliberately stdlib-only -- it duplicates a couple of small helpers from
    utils.py for that reason -- because it is also run directly as a script,
    where a package-relative import would fail."""

    def test_it_runs_as_a_script(self):
        server = HTTPServer(("127.0.0.1", 0), _Accepts)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"

        out = subprocess.run(
            [sys.executable,
             str(REPO_ROOT / "scripts" / "coding_discovery_tools" / "sweep_connectors.py"),
             "--domain", url, "--api-key", "test-key-000000"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
            env={**os.environ, "AI_DISCOVERY_SENTRY_DSN": ""},
        )

        self.assertEqual(
            out.returncode, 0,
            "the sweep could not run as a script, which is the entrypoint its"
            f" stdlib-only imports exist for: {out.stderr[-900:]}",
        )

    def test_it_imports_nothing_from_the_package(self):
        """The guard behind the test above: a package-relative import added here
        would break the script form, and nothing else in the suite would notice
        until a device ran it."""
        source = (REPO_ROOT / "scripts" / "coding_discovery_tools"
                  / "sweep_connectors.py").read_text()
        relative = [line.strip() for line in source.splitlines()
                    if line.strip().startswith(("from .", "from scripts.",
                                                "import scripts."))]

        self.assertEqual(
            relative, [],
            "the sweep gained a package import, so running it as a script now"
            f" fails on the import line: {relative}",
        )


class TheRunStopsWellInsideTheKillGrace(_RunsAFrontDoor):
    """A cross-repo contract. The MDM onboarder runs discovery with its own
    backstop -- the tool's self-timeout plus a grace -- and force-kills the whole
    process group when that passes, which would orphan nothing but does abandon
    whatever the run was reporting.

    This work moved the completed event's deadline out to the watchdog's own fire
    time and let the abort report again, so the run legitimately spends longer
    past its deadline than it used to. It has to stay far inside the grace.
    """

    # setup/mdm/onboard.py: DISCOVERY_KILL_GRACE_SECONDS.
    MDM_KILL_GRACE_SECONDS = 120

    def test_an_unreachable_backend_still_exits_inside_the_grace(self):
        """The worst case for overrun: nothing answers, so every send spends its
        whole budget and the watchdog fires on a run that is still trying."""
        timeout = 20
        # Deliberately far beyond the grace: if the harness were the thing that
        # stopped the run, the assertion below could never fail.
        harness_cap = timeout + 3 * self.MDM_KILL_GRACE_SECONDS
        started = time.monotonic()

        try:
            subprocess.run(
                [sys.executable, "-m", MODULE,
                 "--api-key", "test-key-000000",
                 # A port nothing listens on: connections are refused outright.
                 "--domain", "http://127.0.0.1:1",
                 "--timeout", str(timeout)],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
                timeout=harness_cap, env=self.env(),
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f"still running {harness_cap}s into a {timeout}s budget, so the"
                " MDM backstop would have SIGKILLed it long before this"
            )
        overrun = time.monotonic() - started - timeout

        self.assertLess(
            overrun, self.MDM_KILL_GRACE_SECONDS,
            f"the run ran {overrun:.0f}s past its own {timeout}s deadline, and the"
            f" MDM backstop kills at {self.MDM_KILL_GRACE_SECONDS}s past it -- so it"
            " would be killed mid-report rather than cleaning up and reporting",
        )


if __name__ == "__main__":
    unittest.main()
