"""Result model for audit checks.

Every check emits `CheckResult` objects; a run aggregates them into an
`AuditReport`. Exit-code policy lives here so the CLI and any future
programmatic consumers agree:

- 0: every result is pass or skip (failed *warnings* do not trip CI — the
  warning severity exists precisely for "harmless direction" findings)
- 1: at least one result has status=fail AND severity=error, or the audit
  itself failed (server startup / teardown crash — see AuditReport.error)
- 2: usage error (handled by the CLI before any server is spawned)
- 130: interrupted via SIGINT (handled by the CLI)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

if sys.version_info >= (3, 12):
    from typing import override
else:  # pragma: no cover
    from typing_extensions import override

Severity = Literal["error", "warning"]
Status = Literal["pass", "fail", "skip"]

VALID_SEVERITIES: tuple[str, ...] = ("error", "warning")
VALID_STATUSES: tuple[str, ...] = ("pass", "fail", "skip")

EXIT_OK = 0
EXIT_CHECKS_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130  # 128 + SIGINT


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check against one subject (usually a tool).

    check_id must be stable across releases so CI can suppress it via
    `--skip <ID>`; it is also the key humans grep logs for.
    """

    check_id: str
    severity: Severity
    status: Status
    message: str
    citation: str | None = None
    tool_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"invalid severity {self.severity!r}; expected one of {VALID_SEVERITIES}"
            )
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status {self.status!r}; expected one of {VALID_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "citation": self.citation,
            "tool_name": self.tool_name,
            "details": self.details,
        }

    @override
    def __str__(self) -> str:
        where = f" [{self.tool_name}]" if self.tool_name else ""
        return f"{self.check_id}{where}: {self.status.upper()} ({self.severity}) — {self.message}"


@dataclass
class AuditReport:
    """All results from auditing one server process."""

    server_command: list[str]
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    results: list[CheckResult] = field(default_factory=list)
    tool_version: str = ""
    # Set when the audit itself failed before/around check execution (e.g.
    # the server never initialized); results stays empty in that case.
    error: str | None = None

    @property
    def summary(self) -> dict[str, Any]:
        by_status = {s: 0 for s in VALID_STATUSES}
        failed_by_severity = {s: 0 for s in VALID_SEVERITIES}
        for r in self.results:
            by_status[r.status] += 1
            if r.status == "fail":
                failed_by_severity[r.severity] += 1
        return {
            "total": len(self.results),
            "status": by_status,
            "failed_by_severity": failed_by_severity,
        }

    @property
    def exit_code(self) -> int:
        """0 = all pass/skip; 1 = any failed check with severity=error, or an
        audit-level failure (error detail set)."""
        if self.error is not None:
            return EXIT_CHECKS_FAILED
        return (
            EXIT_CHECKS_FAILED
            if any(r.status == "fail" and r.severity == "error" for r in self.results)
            else EXIT_OK
        )

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(
            {
                "tool": "mcp-audit",
                "version": self.tool_version,
                "server_command": self.server_command,
                "timestamp": self.timestamp,
                "summary": self.summary,
                "exit_code": self.exit_code,
                "error": self.error,
                "results": [r.to_dict() for r in self.results],
            },
            indent=indent,
        )
