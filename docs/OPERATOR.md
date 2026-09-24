# Operator Runbook

Operational guidance for running `mcp-audit` against Model Context Protocol (MCP) stdio servers in development, CI/CD pipelines, and pre-deployment security reviews.

## Pre-requisites & Isolation

- **Python**: 3.11 or newer (`pip install -e ".[dev]"`).
- **Environment**: Audit only servers running inside sandboxed environments (containers, isolated temporary directories, or disposable test harnesses).
- **Permissions**: Grant the minimum filesystem access required for the target server.

## Audit Recipes

### 1. Read-Only Conformance (Safe Default)
Probes only tools asserting `readOnlyHint=true`. Ideal for automated CI gates:

```console
$ mcp-audit run --server "python my_server.py" --strict --json report.json
```

### 2. Targeted Probe Consent
Opt in specific tools to runtime probes without exposing destructive actions globally:

```console
$ mcp-audit run --server "python my_server.py" --allow-tool query_db --allow-tool read_file
```

### 3. Full Security & Destructive Audit
Evaluates path safety (PATHSAFE001) and write probes. Requires explicit sandbox root boundaries via `--arg`:

```console
$ mcp-audit run --server "python my_server.py" \
    --arg "/tmp/sandbox" \
    --allow-destructive \
    --json full-report.json
```

## Exit Code Policy & Pipeline Triage

- `0`: All selected checks passed or skipped. (Under default mode, warnings do not trip failure).
- `1`: One or more `error` findings, any warning under `--strict`, or server startup failure.
- `2`: CLI usage error (e.g. invalid arguments or mutually exclusive flags).
- `130`: Audit interrupted via SIGINT.

## Troubleshooting

- **Server startup timeout**: Increase budget with `--startup-timeout 30.0` if initialization requires model or index loading.
- **Tool call timeouts**: Increase probe budget with `--call-timeout 20.0` for heavy tools.
- **Artifact cleanup**: PATHSAFE001 deletes its probe file upon completion; verify no residual `mcp-audit-probe*` files remain in sandbox directories.
