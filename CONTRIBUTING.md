# Contributing to mcp-audit

We welcome contributions to `mcp-audit`! This project provides conformance checks for Model Context Protocol (MCP) stdio servers.

## Development Setup

Requirements: Python 3.11+ and git.

```console
$ git clone https://github.com/Alpha-W0lf/mcp-audit.git
$ cd mcp-audit
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -e ".[dev]"
```

## Running Tests & Quality Gates

Run the test suite and quality gates before opening a pull request:

```console
# Fast unit tests (107 tests, no subprocesses)
$ pytest -m "not integration"

# Subprocess integration tests with dogfood fixture (121 tests total)
$ export MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py"
$ pytest

# Lint and formatting
$ ruff check .
$ ruff format --check .

# Type checking
$ mypy mcp_audit tests
```

## Principles for New Checks

1. **Read-only by default**: Checks calling server tools must respect the annotation safety gate (`readOnlyHint=true`).
2. **Defect-seeded**: Every check must cite the documented specification rule or upstream bug report that seeded the check class.
3. **Actionable findings**: Results should report the check ID, tool name, advertised contract, and observed runtime behavior.
