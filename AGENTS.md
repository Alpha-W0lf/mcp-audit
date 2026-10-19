# AGENTS.md

mcp-audit is a conformance test kit for MCP stdio servers (Python 3.11+).

## Commands

```console
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -m "not integration"                                           # fast unit tests
MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py" pytest   # full suite, as CI runs it
ruff check . && ruff format --check . && mypy mcp_audit tests
mcp-audit run --server "python tests/fixtures/fixture_server.py"      # watch the live checks fail on purpose
```

## Done means

- The full suite, ruff and mypy pass locally. CI runs the same steps on 3.11 and 3.12.
- A bug fix includes a test that fails on the old code.
- A new or changed live check has a buggy tool in `tests/fixtures/fixture_server.py`, plus a
  test that runs it as a real subprocess and asserts the fail, alongside a correct tool that passes.
- README check table, test counts and `CHANGELOG.md` match the change.
- A claim about a real-server result has its dated run record quoted in `docs/REAL_SERVER_RESULTS.md`.

## Conventions

- Check IDs are public API (CI users skip by ID). Keep existing IDs stable and give new checks
  a new ID through `@check(id=..., severity=..., citation=..., scope=...)` in `mcp_audit/registry.py`.
- Every check cites the upstream issue or rule that seeded it in `citation=`.
- Route every tool call through `mcp_audit.safety.probe_eligibility` and
  `mcp_audit.driver.call_tool_normalized`. Probes that write also require `allow_destructive`.
- Each job has one owner: exit-code policy in `models.py`, sandbox roots via
  `registry.sandbox_roots_from_extra`, argument synthesis via `checks/runtime_required.baseline_arguments`,
  path-argument names via `checks/path_safety.is_path_like_name`.
- Mask user-directory segments in any leaked path a check reports.

## Map

- `mcp_audit/checks/<check>.py` docstring: read when changing a check. It is that check's spec.
- `README.md`: read when changing flags, exit codes or user-facing output.
- `docs/OPERATOR.md`: read when changing CI recipes or sandbox guidance.
- `docs/REAL_SERVER_RESULTS.md`: read when re-running against the official servers.

## Safety

- Run probes against the bundled fixtures, or against servers whose `--arg` points at a temp dir.
- Keep tokens and personal data out of fixtures, tests and reports.
