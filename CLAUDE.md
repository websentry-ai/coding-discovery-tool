# Claude Code Memory

## Zscaler Certificate Constraint

IMPORTANT: **Never use `urllib` or `urllib.request` for HTTP requests in this codebase.** Machines running this tool may have VPNs like Zscaler installed (varies customer to customer), which intercept HTTPS traffic with a custom root CA. Python's `urllib` uses its own bundled CA store and cannot verify these certificates, causing SSL errors. Always use `curl` via `subprocess.run()` instead — curl uses the system certificate store which includes any customer-installed CAs.

```python
# WRONG — will fail on Zscaler machines
import urllib.request
urllib.request.urlopen(req)

# CORRECT — uses system cert store
subprocess.run(["curl", "-s", "-X", "POST", "-H", "Content-Type: application/json", "-d", payload, url], ...)
```

## Testing

- Run tests: `python3 -m pytest tests/ -v`
- Tests use real HTTP servers on localhost — no mocking of HTTP clients
- Mock `time.sleep` to avoid slow retry backoff in tests
- Mock `_SENTRY_DSN` to empty string to prevent real Sentry calls in tests

## Architecture

- `ai_tools_discovery.py` — main entry point, orchestrates detection and reporting
- `utils.py` — shared utilities: HTTP sending (curl), queue persistence, Sentry reporting
- `coding_tool_factory.py` — factory pattern for OS-specific tool detectors/extractors
- `coding_tool_base.py` — base classes for extractors

## Git Workflow

- Never commit directly to main — always create feature branch + PR
