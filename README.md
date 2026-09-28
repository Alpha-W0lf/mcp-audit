# mcp-audit

Conformance test kit that catches the gap between what an [MCP](https://modelcontextprotocol.io)
server *advertises* and what it *actually does* — before that gap ships to production.

An MCP server declares its contract in `tools/list`: input schemas, required
fields, safety hints. A separate code path then enforces — or fails to
enforce — that same contract at runtime. That gap is where real MCP servers
have shipped real bugs: a `required` field the server never checks, a
chunked file reader that corrupts UTF-8 at a byte boundary, a path validator
that accepts Windows-style paths on POSIX, a citation that leaks the
operator's home directory. Point `mcp-audit` at any stdio MCP server and it
runs live, safety-gated probes that reproduce each of these failure classes
and report exactly which one, if any, is present.

**At a glance:** 5 conformance checks · 121 tests (unit + real-subprocess
dogfood, no mocks) · full test/lint/type-check suite green in CI on Python
3.11 and 3.12 · fail-closed probe safety by default · MIT.

## Why

Four of the five checks are seeded by real, public upstream issues — filed
by other engineers against production MCP servers, not by this project. The
fifth (`HYGIENE001`) generalizes a concrete path leak this project's own
earlier work produced. Every check reproduces its failure class as a
runnable fixture and asserts against it, not just against a hypothetical:

| Check | ID | Target defect or bug it catches |
|---|---|---|
| Schema well-formedness | `SCHEMA001` | Static well-formedness of advertised `inputSchema` (`required` ⊆ `properties`, valid types declared). Does **not** catch the runtime #4651 mismatch below — that's `RUNTIME001` — since a schema can be internally consistent and still disagree with what the server enforces. Flags declaration defects before any tool is probed. Seeded by public issue [modelcontextprotocol/servers#4651](https://github.com/modelcontextprotocol/servers/issues/4651) |
| Advertised-vs-runtime required fields (live probe) | `RUNTIME001` | The live #4651 shape: `tools/list` omitted a field from `required` that runtime validation rejected anyway, after a `z.preprocess` refactor changed how zod converts to JSON Schema. Probes omit each advertised-required field in turn and compare runtime behavior against the advertised contract in both directions. Seeded by public issue [modelcontextprotocol/servers#4651](https://github.com/modelcontextprotocol/servers/issues/4651) |
| Multi-byte encoding at chunk boundaries | `ENCODING001` | `headFile`/`tailFile`-style tools that decode fixed-size byte chunks independently corrupt any UTF-8 sequence straddling a chunk boundary. Seeded by public issue [modelcontextprotocol/servers#4666](https://github.com/modelcontextprotocol/servers/issues/4666) |
| Path safety (Windows-style paths on POSIX) | `PATHSAFE001` | `C:\Users\me\file.md` passed validation on a POSIX host and was created as a literal backslash filename inside the sandbox instead of being rejected. Seeded by public issue [modelcontextprotocol/servers#4686](https://github.com/modelcontextprotocol/servers/issues/4686) |
| Citation/path hygiene | `HYGIENE001` | Owner absolute paths leaking into tool outputs — never ship `/Users/you/...` to a client. Generalized from a concrete `/Users/...` path leak this project's own AI-KB MCP tool citations produced; the canonical rule statement is this table row (the MCP spec defines no path-hygiene conformance rule of its own) |

## Status

`v0.5.0` — all five checks implemented, registered, and validated
end-to-end: every failure class above is reproduced by a real, spawned MCP
server in CI (`tests/fixtures/fixture_server.py`), not mocked.

- **Result model** (`models.py`): every finding is a `CheckResult`
  (stable check id, severity, status, message, citation, tool name, details)
  aggregated into an `AuditReport` with JSON serialization.
- **Check registry** (`registry.py`): `@check(id=..., severity=..., citation=...,
  scope=...)`; stable IDs so CI can suppress with `--skip RUNTIME001`.
- **Probe safety** (`safety.py`): live probes run only on tools whose
  server-asserted annotations say `readOnlyHint=true`. `destructiveHint=true`
  or `readOnlyHint=false` tools are skipped unless `--allow-destructive`
  (global override) or `--allow-tool NAME` (per-tool consent for read-shaped
  probes; the gate stays in force for everything else). PATHSAFE001 is a write
  probe and requires `--allow-destructive`. Annotations are hints the server
  asserts about itself — not guarantees; audit only servers you trust.
- **Schema check** (`checks/schema.py`, `SCHEMA001`): static well-formedness
  of advertised `inputSchema` (`required` ⊆ `properties`, valid types
  declared); operates purely statically without calling tools. Seeded during
  investigation of #4651; does not catch the runtime #4651 mismatch (that is
  RUNTIME001), but flags malformed schemas before probe execution.
- **Runtime probe** (`checks/runtime_required.py`, `RUNTIME001`): for each
  eligible tool with required fields, calls it omitting each required field in
  turn. Success ⇒ schema stricter than runtime (warning); error naming a field
  not in `required` ⇒ runtime stricter than advertised (**error** — the #4651
  production shape). Per-call timeouts; baseline-call sanity gate. Baseline
  synthesis honors string `pattern` constraints: candidates are tried in
  order (the field name, `mcp-audit`, a value derived from a simple literal
  prefix like `^thought-` → `thought-mcp-audit`) and accepted only if the
  pattern matches; otherwise the field is unsynthesizable and the baseline is
  skipped rather than probed with an invalid value.
- **Strict mode**: `--strict` makes failed warnings trip exit code 1 too —
  for CI pipelines that want zero tolerated findings (default: only
  severity `error` fails the run). The serialized JSON report carries a
  top-level `strict` boolean so downstream tooling can see which policy
  produced the `exit_code`.
- **Encoding probe** (`checks/encoding.py`, `ENCODING001`): writes a temp
  fixture whose multi-byte marker straddles 1024/2048-byte chunk boundaries,
  then reads it through each file-reading tool; fails on U+FFFD mojibake or a
  missing/mangled marker (#4666). Fixture writes use `tempfile.mkstemp` in
  directories that already exist — the audit tool never creates directories,
  and the unpredictable filename defeats a hostile server pre-placing a
  symlink at a guessed path.
- **Path-safety probe** (`checks/path_safety.py`, `PATHSAFE001`): offers
  `C:\mcp-audit-probe.txt` to the first eligible path-accepting tool on POSIX
  hosts (requires `--allow-destructive`); acceptance is a **fail** (#4686),
  corroborated by locating — and deleting — the literal backslash filename in
  the sandbox root. Rejection is only scored PASS when the error reads as
  path validation AND no probe file was created — an error that merely
  echoes the probe value back is not accepted as evidence of validation.
- **Hygiene probe** (`checks/hygiene.py`, `HYGIENE001`): for each
  probe-eligible tool, makes one benign baseline call (same synthesized
  arguments as RUNTIME001) and scans the returned text for absolute owner
  filesystem paths: POSIX homes (`/Users/<name>/`, `/home/<name>/`), Windows
  user profiles (`C:\Users\<name>\`), and common server-root leaks
  (`/root/`, `/srv/`, `/opt/`, `/var/www/`). Failure names the tool and the pattern
  CLASS only — the user-directory segment is masked (`/Users/<redacted>/…`)
  so the report never re-leaks owner identity into CI logs. Passes when the
  baseline response is clean; skips per tool when gate-ineligible, when
  baseline arguments are unsynthesizable, or when the baseline call fails.
- **Hardened driver** (`driver.py`): bounded startup (default 10 s) with clear
  errors when a server dies before initialize; server stderr captured
  separately so a chatty server can't corrupt results or masquerade as tool
  content.
- **CLI**: `mcp-audit run` / `mcp-audit list-checks`, rich summary tables,
  `--json` machine-readable reports (also emitted on startup failure).
- **Operational guidance**: see [docs/OPERATOR.md](docs/OPERATOR.md) for
  deployment patterns, CI recipes, and sandboxing requirements.

## Install

Requires Python 3.11+. From a clone of this repository:

```console
$ python -m venv .venv
$ . .venv/bin/activate
$ python -m pip install -e ".[dev]"
```

The editable install exposes the `mcp-audit` console command (mapped by
`pyproject.toml` to `mcp_audit.cli:main`).

Optional equivalent if you already use [uv](https://docs.astral.sh/uv/):
`uv pip install -e ".[dev]"`. uv is not required.

### Verify the install

Both commands must exit 0. The second audits the checked-in echo fixture
(five checks against a well-formed, read-only `echo` tool):

```console
$ mcp-audit list-checks
$ mcp-audit run --server "python tests/fixtures/echo_server.py"
```

## Usage

```console
$ mcp-audit list-checks
$ mcp-audit run --server "node dist/index.js" [--arg ARG]... [--skip ID]...
      [--only ID]... [--allow-destructive | --allow-tool NAME]... [--strict]
      [--json PATH] [--startup-timeout SECONDS] [--call-timeout SECONDS]
```

`--arg` values are appended to the server command verbatim; they are also the
only source of *sandbox roots* (the server's allowed directories) used to
place encoding fixtures and corroborate path-safety findings — mcp-audit
never guesses roots out of the server command itself.

`--allow-destructive` additionally opts in to baseline probes on write-shaped
tools. Such a probe calls the tool once with synthesized arguments
(recognizably named `mcp-audit-probe`), and a server that writes may create a
server-side artifact with that name inside its allowed root. This is by
design and operator-consented — run it only against sandboxed servers, and
delete any `mcp-audit-probe` artifact afterwards if the server persists it.

`--allow-tool NAME` is the granular alternative to `--allow-destructive` for
the annotation gate: it permits runtime probes (RUNTIME001, ENCODING001,
HYGIENE001) against the named tools only (repeatable), leaving the gate in
force for everything else. PATHSAFE001 is a write probe and still requires
`--allow-destructive` — `--allow-tool` does not enable it. The two flags
are mutually exclusive — passing both is a usage error (exit 2). The same
side-effect caveat applies: only name tools on servers you trust.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | every result is pass or skip (failed *warnings* do not trip CI unless `--strict`) |
| `1` | a failed check with severity `error`, any failed check under `--strict`, server startup failure, or an unexpected teardown crash |
| `2` | usage error (bad flags, unknown check IDs, unparseable `--server`, `--allow-tool` combined with `--allow-destructive`) |
| `130` | interrupted via SIGINT |

Test suite:

```console
$ pytest -m "not integration"   # unit tests only (107 passed; no subprocesses)
$ pytest                        # unit + standalone integration (119 passed, 2 skipped)
$ export MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py"
$ pytest                        # full suite including dogfood against buggy fixture (121 passed)
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
   `--allow-destructive` or consent per tool with `--allow-tool NAME` (read probes
   only; PATHSAFE001 write probe always requires `--allow-destructive`).
2. **Every check cites its defect or rule.** Each check links to the upstream
   issue that seeded it or to the documented conformance/hygiene rule it enforces.
3. **Minimal output, actionable failures.** Each failure names the field, the
   advertised schema, and the observed behavior.

## License

MIT — see [LICENSE](LICENSE).
