"""CLI entry points: `mcp-audit run` and `mcp-audit list-checks`.

Exit codes (see mcp_audit.models): 0 all pass/skip; 1 any failed check with
severity=error (failed warnings do NOT trip CI); 2 usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import traceback

from rich.console import Console
from rich.table import Table

from mcp_audit import __version__
from mcp_audit.driver import ServerStartupError, connect
from mcp_audit.models import (
    EXIT_OK,
    EXIT_USAGE,
    AuditReport,
    CheckResult,
)
from mcp_audit.registry import REGISTRY, CheckContext, load_checks

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-audit",
        description="Conformance test kit for MCP servers.",
    )
    parser.add_argument(
        "--version", action="version", version=f"mcp-audit {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="audit a server over stdio")
    p_run.add_argument(
        "--server",
        required=True,
        help='server launch command, shell-quoted, e.g. "node dist/index.js"',
    )
    p_run.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="ARG",
        help="additional argument appended to the server command (repeatable)",
    )
    p_run.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="ID",
        help="check ID to suppress (repeatable)",
    )
    p_run.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID",
        help="restrict to this check ID (repeatable)",
    )
    p_run.add_argument(
        "--allow-destructive",
        action="store_true",
        help="probe tools even when annotations say they may be destructive "
        "(use only against sandboxed servers)",
    )
    p_run.add_argument("--json", metavar="PATH", help="write the JSON report here")
    p_run.add_argument(
        "--startup-timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="server initialize budget (default 10)",
    )
    p_run.add_argument(
        "--call-timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="per-tool-call probe budget (default 10)",
    )

    sub.add_parser("list-checks", help="list registered checks and citations")
    return parser


def _unknown_ids(skip: list[str], only: list[str]) -> list[str]:
    known = set(REGISTRY.ids())
    return [i for i in (*skip, *only) if i not in known]


def _select_checks(skip: list[str], only: list[str]):
    load_checks()
    unknown = _unknown_ids(skip, only)
    if unknown:
        raise ValueError(f"unknown check id(s): {', '.join(unknown)}")
    selected = REGISTRY.all()
    if only:
        selected = [c for c in selected if c.id in set(only)]
    if skip:
        selected = [c for c in selected if c.id not in set(skip)]
    return selected


async def run_checks(
    command: list[str],
    *,
    skip: list[str] | None = None,
    only: list[str] | None = None,
    allow_destructive: bool = False,
    startup_timeout: float = 10.0,
    call_timeout: float = 10.0,
) -> AuditReport:
    """Spawn server, execute selected checks, return the report."""
    specs = _select_checks(list(skip or []), list(only or []))
    report = AuditReport(server_command=list(command), tool_version=__version__)

    async with connect(command, startup_timeout=startup_timeout) as handle:
        ctx = CheckContext(
            session=handle.session,
            tools=handle.tools,
            allow_destructive=allow_destructive,
            call_timeout=call_timeout,
        )
        for spec in specs:
            try:
                produced = await spec.fn(ctx)
            except Exception as e:  # noqa: BLE001 — a crashing check must not abort the audit
                produced = [
                    CheckResult(
                        check_id=spec.id,
                        severity=spec.severity,
                        status="fail",
                        message=f"check crashed: {type(e).__name__}: {e}",
                        citation=spec.citation,
                        details={"traceback": traceback.format_exc()},
                    )
                ]
            if produced is None:
                continue
            report.results.extend(
                produced if isinstance(produced, list) else [produced]
            )

    return report


def render_report(report: AuditReport) -> None:
    table = Table(title="mcp-audit results", title_justify="left", expand=False)
    for col, opts in [
        ("check", {"no_wrap": True}),
        ("sev", {"no_wrap": True}),
        ("status", {"no_wrap": True}),
        ("tool", {}),
        ("message", {}),
    ]:
        table.add_column(col, **opts)

    style_for_status = {"pass": "green", "fail": "red", "skip": "yellow"}
    for r in sorted(report.results, key=lambda x: (x.check_id, x.tool_name or "")):
        table.add_row(
            r.check_id,
            r.severity,
            f"[{style_for_status[r.status]}]{r.status}[/]",
            r.tool_name or "—",
            r.message if len(r.message) <= 120 else r.message[:117] + "...",
        )
    console.print(table)

    s = report.summary
    console.print(
        f"[bold]{s['total']}[/] checks · "
        f"[green]{s['status']['pass']} pass[/] · "
        f"[red]{s['status']['fail']} fail[/] "
        f"(error:{s['failed_by_severity']['error']}, "
        f"warning:{s['failed_by_severity']['warning']}) · "
        f"[yellow]{s['status']['skip']} skip[/]"
    )


def cmd_run(args: argparse.Namespace) -> int:
    try:
        command = shlex.split(args.server) + list(args.arg)
    except ValueError as e:
        print(f"mcp-audit: cannot parse --server: {e}", file=sys.stderr)
        return EXIT_USAGE
    if not command:
        print("mcp-audit: --server must not be empty", file=sys.stderr)
        return EXIT_USAGE

    load_checks()
    unknown = _unknown_ids(list(args.skip), list(args.only))
    if unknown:
        print(
            f"mcp-audit: unknown check id(s): {', '.join(unknown)} "
            f"(registered: {', '.join(REGISTRY.ids())})",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        report = asyncio.run(
            run_checks(
                command,
                skip=args.skip,
                only=args.only,
                allow_destructive=args.allow_destructive,
                startup_timeout=args.startup_timeout,
                call_timeout=args.call_timeout,
            )
        )
    except ServerStartupError as e:
        print(f"mcp-audit: server failed to start:\n{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nmcp-audit: interrupted", file=sys.stderr)
        return EXIT_USAGE

    render_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(report.to_json() + "\n")
        console.print(f"[dim]report written to {args.json}[/]")
    return report.exit_code


def cmd_list_checks() -> int:
    load_checks()
    table = Table(title="registered checks", title_justify="left")
    table.add_column("id", no_wrap=True)
    table.add_column("scope", no_wrap=True)
    table.add_column("default severity", no_wrap=True)
    table.add_column("citation", no_wrap=False)
    for spec in REGISTRY.all():
        table.add_row(spec.id, spec.scope, spec.severity, spec.citation or "—")
    console.print(table)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "list-checks":
        return cmd_list_checks()
    parser.error(f"unknown command {args.command!r}")  # exits 2
    return EXIT_USAGE  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
