# Security Policy

## Reporting a Vulnerability

If you discover a security issue or vulnerability in `mcp-audit`, please report it responsibly.

- **Email**: Contact Tom Chacko directly via GitHub security advisories or email at `tomchacko@gmail.com`.
- **Response time**: You will receive an acknowledgment within 48 hours.
- **Coordination**: We ask that you give us reasonable time to investigate and address the vulnerability before public disclosure.

## Security Posture & Probing Safeguards

`mcp-audit` executes live probes against MCP servers. By default, it operates in a read-only-by-default posture:
- Runtime probes only call tools that advertise `readOnlyHint=true`.
- Destructive and unannotated tools are skipped unless `--allow-destructive` or `--allow-tool <name>` is explicitly supplied.
- PATHSAFE001 requires `--allow-destructive` and only probes tools inside explicitly supplied sandbox roots (`--arg`).
- HYGIENE001 masks user-directory segments (`/Users/<redacted>/…`) in all findings to avoid re-leaking sensitive local paths in CI logs or reports.
