# mcp-audit

Conformance test kit for [Model Context Protocol](https://modelcontextprotocol.io) servers.

Point it at any MCP server (stdio transport) and run assertions against what it
*advertises* versus what it *accepts* — the mismatch class that ships to
production more often than anyone expects.

## Why

Every check in this kit is seeded by a real bug found in the wild:

| Check | ID | Real bug it catches |
|---|---|---|
| Schema well-formedness | `SCHEMA001` | [modelcontextprotocol/servers#4651](https://github.com/modelcontextprotocol/servers/issues/4651) — `tools/list` omitted a field from `required` that runtime validation rejected, after a `z.preprocess` refactor changed zod-to-JSON-Schema conversion |
| Advertised-vs-runtime required fields (live probe) | `RUNTIME001` | Same #4651 bug class, caught dynamically: probes omit advertised-required fields on `readOnlyHint` tools and compare runtime behavior against the advertised contract in both directions |
| Multi-byte encoding at chunk boundaries | `ENCODING001` | [modelcontextprotocol/servers#4666](https://github.com/modelcontextprotocol/servers/issues/4666) — `headFile`/`tailFile` corrupted UTF-8 sequences straddling 1024-byte read boundaries |
| Path safety (Windows-style paths on POSIX) | `PATHSAFE001` | [modelcontextprotocol/servers#4686](https://github.com/modelcontextprotocol/servers/issues/4686) — `C:\Users\me\file.md` passed validation and was created as a literal backslash filename inside the sandbox |
| Citation/path hygiene | *planned* | Owner absolute paths leaking into tool outputs (never ship `/Users/you/...` to clients) |

## Status

`v0.3` — all four checks implemented, registered, and dogfooded against real
upstream servers:

- **Result model** (`models.py`): every finding is a `CheckResult`
  (stable check id, severity, status, message, citation, tool name, details)
  aggregated into an `AuditReport` with JSON serialization.
- **Check registry** (`registry.py`): `@check(id=..., severity=..., citation=...,
  scope=...)`; stable IDs so CI can suppress with `--skip RUNTIME001`.
- **Probe safety** (`safety.py`): live probes run only on tools whose
  server-asserted annotations say `readOnlyHint=true`. `destructiveHint=true`
  or `readOnlyHint=false` tools are skipped unless `--allow-destructive`.
  Annotations are hints the server asserts about itself — not guarantees;
  audit only servers you trust.
- **Runtime probe** (`checks/runtime_required.py`, `RUNTIME001`): for each
  eligible tool with required fields, calls it omitting each required field in
  turn. Success ⇒ schema stricter than runtime (warning); error naming a field
  not in `required` ⇒ runtime stricter than advertised (**error** — the #4651
  production shape). Per-call timeouts; baseline-call sanity gate.
- **Encoding probe** (`checks/encoding.py`, `ENCODING001`): writes a temp
  fixture whose multi-byte marker straddles 1024/2048-byte chunk boundaries,
  then reads it through each file-reading tool; fails on U+FFFD mojibake or a
  missing/mangled marker (#4666). Fixture writes use `tempfile.mkstemp` in
  directories that already exist — the audit tool never creates directories.
- **Path-safety probe** (`checks/path_safety.py`, `PATHSAFE001`): offers
  `C:\mcp-audit-probe.txt` to the first eligible path-accepting tool on POSIX
  hosts (requires `--allow-destructive`); acceptance is a **fail** (#4686),
  corroborated by locating — and deleting — the literal backslash filename in
  the sandbox root. Rejection is only scored PASS when the error reads as
  path validation AND no probe file was created.
- **Hardened driver** (`driver.py`): bounded startup (default 10 s) with clear
  errors when a server dies before initialize; server stderr captured
  separately so chatty servers can't corrupt results or reports.
- **CLI**: `mcp-audit run` / `mcp-audit list-checks`, rich summary tables,
  `--json` machine-readable reports (also emitted on startup failure).

The citation/path-hygiene probe remains specified but unimplemented.

## Usage

```console
$ mcp-audit list-checks
$ mcp-audit run --server "node dist/index.js" [--arg ARG]... [--skip ID]...
      [--only ID]... [--allow-destructive] [--json PATH]
      [--startup-timeout SECONDS] [--call-timeout SECONDS]
```

`--arg` values are appended to the server command verbatim; they are also the
only source of *sandbox roots* (the server's allowed directories) used to
place encoding fixtures and corroborate path-safety findings — mcp-audit
never guesses roots out of the server command itself.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | every result is pass or skip (failed *warnings* do not trip CI) |
| `1` | a failed check with severity `error`, server startup failure, or an unexpected teardown crash |
| `2` | usage error (bad flags, unknown check IDs, unparseable `--server`) |
| `130` | interrupted via SIGINT |

Test suite:

```console
$ pytest -m "not integration"   # unit tests only (no subprocesses)
$ pytest                        # everything, including subprocess integration tests
$ export MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py"
$ pytest -m integration         # + dogfood suite against the buggy fixture server
```

Library use:

```python
import asyncio
from mcp_audit.cli import run_checks

report = asyncio.run(run_checks(["node", "dist/index.js"]))
print(report.summary, report.exit_code)
print(report.to_json())
```

## Principles

1. **Read-only by default.** Runtime probing can execute side effects; probes
   call only tools whose server-asserted annotations claim `readOnlyHint=true`,
   and never tools annotated destructive — unless you pass
   `--allow-destructive`.
2. **Every check cites its bug.** If a check exists, a real server shipped the bug.
3. **Minimal output, actionable failures.** Each failure names the field, the
   advertised schema, and the observed behavior.

## License

MIT — see [LICENSE](LICENSE).
