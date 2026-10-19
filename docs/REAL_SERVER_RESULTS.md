# Results on the official MCP reference servers

mcp-audit flagged two known bugs
([#4651](https://github.com/modelcontextprotocol/servers/issues/4651),
[#4686](https://github.com/modelcontextprotocol/servers/issues/4686)) in the
official MCP servers before their fixes shipped. Other people filed both
issues. mcp-audit was run against two servers from
[modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers):
`sequentialthinking` and `filesystem`.

## Runs

| When (UTC) | mcp-audit | Servers tree | Record |
|---|---|---|---|
| 2026-08-25 and 2026-08-26, **before the fixes** | v0.4.0, then v0.5.0 | A local checkout named `servers-4686`, taken before either fix merged. | Quoted below (records 1 and 2) |
| 2026-09-24, re-run | v0.4.0 and v0.5.0 | Commit [`40a8de8`](https://github.com/modelcontextprotocol/servers/commit/40a8de87ed31a307c2a85cae21a78da9b7c1caf8), the base of the #4651 fix. Both bugs were still present. | Quoted below (records 3 and 4) |

Upstream timeline, from GitHub:

| Date (UTC) | Event |
|---|---|
| 2026-08-17 | #4651 filed |
| 2026-08-23 | #4686 filed |
| 2026-08-25 23:29 and 2026-08-26 01:26 | mcp-audit flags both bugs (records 1 and 2 below) |
| 2026-08-28 17:57 | #4651 fixed by [#4652](https://github.com/modelcontextprotocol/servers/pull/4652) |
| 2026-08-30 21:43 | #4686 fixed by [#4704](https://github.com/modelcontextprotocol/servers/pull/4704) |

## Results

Every run flagged both bugs and exited with code 1. HYGIENE001 arrived
in v0.5.0, so the v0.4.0 runs did not include it. The RUNTIME001 message
below is copied from the 2026-09-24 run.

| Server | Check | Result | What it means |
|---|---|---|---|
| `sequentialthinking` | RUNTIME001 | **flagged (error)** | `runtime validation requires ['nextThoughtNeeded'] but inputSchema.required omits them (advertised required: ['thought', 'thoughtNumber', 'totalThoughts'])`. This is the #4651 bug. |
| `filesystem` | PATHSAFE001 | **flagged (error)** | `write_file` accepted `C:\mcp-audit-probe.txt` on POSIX and created a file with that literal name. This is the #4686 bug. |
| `filesystem` | HYGIENE001 | pass or skip | No false positives. The server's sandbox paths (for example `/private/tmp/...`) were not flagged. |

mcp-audit also confirmed the fixes once they shipped. On 2026-09-24,
`@modelcontextprotocol/server-sequential-thinking` and
`@modelcontextprotocol/server-filesystem` 2026.8.31 both passed, while the
older `server-filesystem@2026.7.10` was still flagged by PATHSAFE001
(record 3).

## Run records

These run records were written during development, at the UTC times
shown, and are quoted here word for word. `servers-4686` was the name of
the local checkout of the servers repo. In these records, "this PR" means the
change under test, and in record 4, "#1" means the v0.4.0 run (record 3).

**Record 1.** v0.4.0 release check, recorded 2026-08-25T23:29:58Z.

> ## Release bar (verified identical, servers-4686 @ dist)
>
> | Server | Expectation | Result |
> |---|---|---|
> | sequentialthinking | RUNTIME001 fail (error), exit 1 | ✅ `runtime validation requires ['nextThoughtNeeded'] but inputSchema.required omits them` |
> | filesystem | PATHSAFE001 fail (`write_file`, #4686), ENCODING001 pass/skip, exit 1 | ✅ PATHSAFE001 fail; ENCODING001 pass on `read_file`/`read_text_file`, skip elsewhere |

**Record 2.** v0.5.0 release check, recorded 2026-08-26T01:26:11Z.

> ## Release-bar regression results (v0.5, official servers)
>
> ### filesystem (`servers-4686/src/filesystem`, `--allow-destructive`)
> - **PATHSAFE001: FAIL** ✅ expected — `write_file` accepted `C:\mcp-audit-probe.txt` on POSIX and created a literal backslash filename.
> - **ENCODING001: PASS/SKIP** ✅ expected — pass on `read_file`/`read_text_file`; skips on non-text surfaces and unsynthesizable probes.
> - **HYGIENE001 (new): no false positives** — 8 pass (all text-returning tools incl. `list_allowed_directories`), 5 skip where the baseline call itself failed. The server's sandbox-root responses (e.g. `/private/tmp/...`) are correctly not flagged since they are neither owner-home nor listed server-web roots.
> - Report carries `strict: false`, `exit_code: 1`.
>
> ### sequentialthinking (`servers-4686/src/sequentialthinking`)
> - **RUNTIME001: FAIL** ✅ expected — runtime requires `nextThoughtNeeded` but `inputSchema.required` omits it.
> - **HYGIENE001: skip** — baseline call fails (missing required field), nothing to scan; correct per design.
> - `exit_code: 1`.
>
> All gates green locally (pytest 118 passed / integration green, ruff clean, mypy green) and CI `quality` pass on this PR.

**Record 3.** Re-run of v0.4.0, recorded 2026-09-24T04:02:52Z.

> The original body used `servers-4686 @ dist`. That tree is not in this repo. I reconstructed it as [modelcontextprotocol/servers@40a8de8](https://github.com/modelcontextprotocol/servers/commit/40a8de87ed31a307c2a85cae21a78da9b7c1caf8) (base of servers#4652; both #4651 and #4686 still present), `npm ci` + `tsc` for the two workspaces, then `node …/dist/index.js`.
>
> | Server | Expectation | Result |
> |---|---|---|
> | sequentialthinking (`src/sequentialthinking/dist`) | RUNTIME001 fail (error), exit 1 | **Verified** `runtime validation requires ['nextThoughtNeeded'] but inputSchema.required omits them (advertised required: ['thought', 'thoughtNumber', 'totalThoughts'])` |
> | filesystem (`src/filesystem/dist`, `--allow-destructive`, sandbox `/tmp/mcp-audit-sandbox-v04`) | PATHSAFE001 fail (`write_file`, #4686), ENCODING001 pass/skip, exit 1 | **Verified** PATHSAFE001 fail: accepted `C:\\mcp-audit-probe.txt` on POSIX and created the literal backslash filename; ENCODING001 pass on `read_file` / `read_text_file`, skip elsewhere |

Later in the same record:

> Current published npm (`@modelcontextprotocol/server-sequential-thinking` and `server-filesystem` **2026.8.31**) no longer reproduce those fails — upstream #4651 / #4686 are closed. That is an upstream change, not a regression in this PR. `server-filesystem@2026.7.10` still fails PATHSAFE001; published `server-sequential-thinking@2026.7.4` already advertises four required fields (RUNTIME001 pass).

**Record 4.** Re-run of v0.5.0, recorded 2026-09-24T04:02:53Z.

> Same reconstructed snapshot as #1: [modelcontextprotocol/servers@40a8de8](https://github.com/modelcontextprotocol/servers/commit/40a8de87ed31a307c2a85cae21a78da9b7c1caf8) built to `dist/` (the `servers-4686` tree is not in this repo).
>
> **filesystem** (`src/filesystem/dist`, `--allow-destructive`):
> - **PATHSAFE001: FAIL** — `write_file` accepted `C:\\mcp-audit-probe.txt` on POSIX and created a literal backslash filename. **Verified**
> - **ENCODING001: PASS/SKIP** — pass on `read_file` / `read_text_file`; skip elsewhere. **Verified**
> - **HYGIENE001: no false positives** — 5 pass (`read_multiple_files`, `write_file`, `edit_file`, `get_file_info`, `list_allowed_directories`), 9 skip where the baseline call itself failed (sandbox already dirty from the PATHSAFE probe). Report `strict: false`, `exit_code: 1`. **Verified**
>
> **sequentialthinking** (`src/sequentialthinking/dist`):
> - **RUNTIME001: FAIL** — `runtime validation requires ['nextThoughtNeeded'] but inputSchema.required omits them`. **Verified**
> - **HYGIENE001: skip** — baseline call fails (missing required field); nothing to scan. **Verified**
> - `exit_code: 1`

## Reproduce

1. Check out the servers repo at `40a8de8`. Run `npm ci`, then build
   `src/sequentialthinking` and `src/filesystem` with `tsc`.
2. Run mcp-audit from the servers repo root:

```console
$ mcp-audit run --server "node src/sequentialthinking/dist/index.js"
$ mkdir -p /tmp/mcp-audit-sandbox
$ mcp-audit run --server "node src/filesystem/dist/index.js" \
    --arg /tmp/mcp-audit-sandbox --allow-destructive
```
