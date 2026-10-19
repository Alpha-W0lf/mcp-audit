# Security Policy

## Report a vulnerability

Report it privately through GitHub: open this repository's **Security** tab
and choose **Report a vulnerability**. Please do not open a public issue for
a security problem. You will get an acknowledgment within 48 hours.
Please allow time for a fix before disclosing publicly.

## What mcp-audit does to the server it audits

- **Read-only by default.** It calls only tools that declare
  `readOnlyHint=true`. Tools with no hint, or with `destructiveHint=true`,
  are skipped.
- **Consent flags.** `--allow-tool NAME` allows probes on one named tool.
  `--allow-destructive` allows probes on every tool. A write-capable tool
  probed this way may keep a file or record named `mcp-audit-probe`.
- **One write probe.** PATHSAFE001 sends one Windows-style path to one tool.
  It runs only with `--allow-destructive`. If you pass the server's allowed
  directory with `--arg`, mcp-audit also confirms whether a stray file was
  created and deletes it. Without `--arg`, the result is still reported, but
  nothing can be cleaned up.
- **Temporary files.** ENCODING001 writes one test file with an unpredictable
  name, in an `--arg` directory if given or the system temp folder otherwise.
  It deletes the file when the check ends. mcp-audit never creates
  directories.
- **No re-leaking.** HYGIENE001 masks user names in any leaked path it
  reports (`/Users/<redacted>/…`).

Tool hints are claims the server makes about itself. Audit servers you
trust, and run destructive probes only in a sandbox.
