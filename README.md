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
| Multi-byte encoding at chunk boundaries | *planned* (`ENCODING001`) | [modelcontextprotocol/servers#4666](https://github.com/modelcontextprotocol/servers/issues/4666) — `headFile`/`tailFile` corrupted UTF-8 sequences straddling 1024-byte read boundaries |
| Path safety (Windows-style paths on POSIX) | *planned* (`PATHSAFE001`) | [modelcontextprotocol/servers#4686](https://github.com/modelcontextprotocol/servers/issues/4686) — `C:\Users\me\file.md` passed validation and was created as a literal backslash filename inside the sandbox |
| Citation/path hygiene | *planned* | Owner absolute paths leaking into tool outputs (never ship `/Users/you/...` to clients) |

## Status

`v0.2` — core architecture landed:

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
- **Hardened driver** (`driver.py`): bounded startup (default 10 s) with clear
  errors when a server dies before initialize; server stderr captured
  separately so chatty servers can't corrupt results or reports.
- **CLI**: `mcp-audit run` / `mcp-audit list-checks`, rich summary tables,
  `--json` machine-readable reports.

Encoding, path-safety, and hygiene probes remain specified but unimplemented
(see module docstrings in `mcp_audit/checks/`).

## Usage

```console
$ mcp-audit list-checks
$ mcp-audit run --server "node dist/index.js" --arg /allowed/root \
      [--skip SCHEMA001] [--only RUNTIME001] [--allow-destructive] [--json out.json]
```

Exit codes: `0` all checks pass or skip · `1` any failed check with severity
`error` (failed *warnings* do not trip CI) · `2` usage error.

Dogfood integration test against a deliberately-buggy fixture server:

```console
$ export MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py"
$ pytest -m integration
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
