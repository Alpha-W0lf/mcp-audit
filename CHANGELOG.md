# Changelog

All notable changes to mcp-audit. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic versioning.

## [0.3.0]

### Added

- **ENCODING001** implemented: probes file-reading tools with a fixture whose
  multi-byte UTF-8 marker straddles 1024/2048-byte chunk boundaries; fails on
  U+FFFD mojibake or a missing/mangled marker (servers#4666).
- **PATHSAFE001** implemented: probes path-accepting tools with a Windows
  drive-letter path on POSIX hosts; fails when accepted, corroborates by
  locating the literal backslash filename inside the sandbox root, and cleans
  it up (servers#4686).
- Shared `call_tool_normalized` probe primitive in `driver.py` used by all
  runtime checks.
- `--startup-timeout` / `--call-timeout` CLI flags documented; exit code 130
  on SIGINT.
- `AuditReport.error`: startup failures now still produce a JSON report (zero
  results + error detail) with exit code 1.
- SCHEMA001/RUNTIME001 emit an explanatory skip when a server advertises no
  tools.

### Fixed

- mcp-audit no longer creates filesystem side effects of its own: sandbox
  roots come only from explicit `--arg` values (never guessed from server
  command tokens), and ENCODING001 writes fixtures with `tempfile.mkstemp`
  into pre-existing directories only — it never calls `mkdir`.
- PATHSAFE001 no longer scores PASS when an unrelated error merely echoes the
  probe path: PASS now requires rejection vocabulary AND confirmation that no
  literal probe file was created in a known sandbox root.
- `load_checks()` raises if the registry is empty after importing built-ins.
- Teardown-time anyio `BaseExceptionGroup`s surface as a clean diagnostic +
  exit 1 instead of a raw traceback.
- `CheckResult` is now a frozen dataclass, consistent with `CheckSpec`,
  `ProbeDecision`, and `AdvertisedTool`.

### Changed

- Requires `mcp>=2.0.0` (fixtures and tests use the 2.x
  constructor-registered-handler API).
- Annotation canonicalization (SDK snake_case -> wire camelCase) centralized
  in `driver.py`; `safety.py` consumes plain dicts only.
- All subprocess-spawning tests are uniformly marked `integration`.
- Ruff configuration added (line-length 100, py311, E/F/I/W/BLE/SIM).

## [0.2.0]

### Added

- Result model (`CheckResult`/`AuditReport`) with JSON serialization and a
  documented exit-code policy.
- Check registry with stable IDs (`@check(id=..., severity=..., citation=...,
  scope=...)`); CI can suppress by ID via `--skip`.
- Probe safety gate (`safety.py`): live probes only on tools asserting
  `readOnlyHint=true`, unless `--allow-destructive`.
- **RUNTIME001**: live probe comparing advertised `required` fields against
  runtime-enforced arguments (servers#4651), with constraint-aware argument
  synthesis and per-call timeouts.
- Hardened driver: bounded startup with captured stderr tails; server stderr
  isolated from reports.
- CLI: `mcp-audit run` / `mcp-audit list-checks`, rich tables, `--json`
  reports; dogfood integration test against a deliberately-buggy fixture.

## [0.1.0]

### Added

- Initial scaffold: stdio driver and **SCHEMA001** static inputSchema
  well-formedness check (servers#4651), plus written specifications for the
  encoding (#4666) and path-safety (#4686) checks.
