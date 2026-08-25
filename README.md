# mcp-audit

Conformance test kit for [Model Context Protocol](https://modelcontextprotocol.io) servers.

Point it at any MCP server (stdio transport) and run assertions against what it
*advertises* versus what it *accepts* — the mismatch class that ships to
production more often than anyone expects.

## Why

Every check in this kit is seeded by a real bug found in the wild:

| Check class | Real bug it catches |
|---|---|
| Schema/runtime required-field compliance | [modelcontextprotocol/servers#4651](https://github.com/modelcontextprotocol/servers/issues/4651) — `tools/list` omitted a field from `required` that runtime validation rejected, after a `z.preprocess` refactor changed zod-to-JSON-Schema conversion |
| Multi-byte encoding at chunk boundaries | [modelcontextprotocol/servers#4666](https://github.com/modelcontextprotocol/servers/issues/4666) — `headFile`/`tailFile` corrupted UTF-8 sequences straddling 1024-byte read boundaries |
| Path safety (Windows-style paths on POSIX) | [modelcontextprotocol/servers#4686](https://github.com/modelcontextprotocol/servers/issues/4686) — `C:\Users\me\file.md` passed validation and was created as a literal backslash filename inside the sandbox |
| Citation/path hygiene | Owner absolute paths leaking into tool outputs (never ship `/Users/you/...` to clients) |

## Status

`v0.1` — schema-compliance checks are implemented; encoding, path-safety, and
hygiene probes are specified (see `mcp_audit/checks/`) with implementations landing.

## Usage

```python
from mcp_audit.driver import stdio_server
from mcp_audit.checks.schema import check_schema_well_formed

async with stdio_server(["node", "dist/index.js", "/allowed/root"]) as tools:
    for tool in tools:
        problems = check_schema_well_formed(tool)
```

## Principles

1. **Read-only by default.** Runtime probing can execute side effects; probes
   that call tools run only against an explicit allowlist.
2. **Every check cites its bug.** If a check exists, a real server shipped the bug.
3. **Minimal output, actionable failures.** Each failure names the field, the
   advertised schema, and the observed behavior.
