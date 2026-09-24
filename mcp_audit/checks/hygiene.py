"""HYGIENE001 — absolute owner filesystem paths leaking into tool outputs.

The fourth bug class in the README table: tools that return content,
citations, or paths must never ship machine-local absolute paths to clients.
A citation like ``/Users/tom/Documents/private/doc.md`` leaks the owner's
identity and directory layout to every client that receives it; the correct
shape is a stable, server-relative identifier (``source_id: docs/report``).

Citation choice: unlike SCHEMA001/RUNTIME001/ENCODING001/PATHSAFE001 there is
no single upstream issue to cite — this check generalizes concrete
``/Users/...`` path leaks in AI-KB MCP tool citations that seeded the
project, and the canonical statement of the rule lives in this repo's own
README bug table. The citation therefore links the README row (the MCP spec
defines no path-hygiene conformance rule, so the modelcontextprotocol spec
URL would not document *this* bug class).

Protocol per probe-eligible tool (same readOnlyHint safety gate as every
runtime check):

1. Synthesize benign baseline arguments (shared with RUNTIME001). Tools whose
   required fields are unsynthesizable are skipped rather than probed with an
   invalid value.
2. Make ONE baseline call and scan the returned text for absolute owner
   filesystem paths: POSIX home directories (``/Users/<name>/``,
   ``/home/<name>/``), Windows user profiles (``C:\\Users\\<name>\\``), and
   common server-root leakage (``/root/``, ``/srv/``, ``/opt/``,
   ``/var/www/``).
3. FAIL (severity=error) naming the tool and the leaked path pattern CLASS.
   The full leaked path is never included in the message: the user-directory
   segment is masked (``/Users/<redacted>/…``) so the report itself does not
   re-leak owner identity into CI logs.
4. PASS when the baseline response is clean; SKIP per tool when the tool is
   gate-ineligible, the baseline is unsynthesizable, or the baseline call
   fails (a failed call returns nothing to scan).
"""

from __future__ import annotations

import re
from typing import Any

from mcp_audit.checks.runtime_required import baseline_arguments
from mcp_audit.driver import AdvertisedTool, call_tool_normalized
from mcp_audit.models import MAX_ERROR_TEXT, CheckResult, Severity
from mcp_audit.registry import CheckContext, check
from mcp_audit.safety import probe_eligibility

CHECK_ID = "HYGIENE001"
SEVERITY: Severity = "error"

# No single upstream issue seeds this check (see module docstring); cite the
# project's own README bug-table row that specifies the concrete leak rule.
CITATION_HYGIENE = "https://github.com/Alpha-W0lf/mcp-audit#why"

# Pattern classes detected in tool response text. Each regex captures the
# path shape; the user-directory segment (group 1, where present) is masked
# before anything is recorded.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("posix_home_path", re.compile(r"/(?:Users|home)/([^/\s:'\"`]+)/")),
    ("windows_user_path", re.compile(r"[A-Za-z]:\\Users\\([^\\\s:'\"`]+)\\", re.IGNORECASE)),
    ("server_root_path", re.compile(r"/(?:root|srv|opt|var/www)/")),
)


def scan_for_leaks(text: str) -> list[tuple[str, str]]:
    """Scan response text for absolute owner paths.

    Returns (pattern_class, masked_example) pairs, deduplicated and in first-
    seen order. Masked examples NEVER contain the real user-directory
    segment: ``/Users/tom/…`` becomes ``/Users/<redacted>/…``.
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern_class, rx in _PATTERNS:
        for m in rx.finditer(text):
            if pattern_class == "posix_home_path":
                # m.group(0) is "/Users/<name>/" or "/home/<name>/": keep the
                # root segment, mask the user segment.
                root = m.group(0)[1:].split("/", 1)[0]
                masked = f"/{root}/<redacted>/…"
            elif pattern_class == "windows_user_path":
                masked = f"{m.group(0)[0]}:\\Users\\<redacted>\\…"
            else:
                masked = m.group(0) + "…"
            pair = (pattern_class, masked)
            if pair not in seen:
                seen.add(pair)
                found.append(pair)
    return found


def _result(
    status: str,
    message: str,
    tool_name: str | None,
    details: dict[str, Any],
) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        severity=SEVERITY,
        status=status,  # type: ignore[arg-type]
        message=message,
        citation=CITATION_HYGIENE,
        tool_name=tool_name,
        details=details,
    )


async def _probe_tool(ctx: CheckContext, tool: AdvertisedTool) -> CheckResult:
    decision = probe_eligibility(
        tool, allow_destructive=ctx.allow_destructive, allow_tools=ctx.allow_tools
    )
    if not decision.eligible:
        return _result(
            "skip",
            f"not probed: {decision.reason}",
            tool.name,
            {"skip_reason": decision.outcome},
        )

    base_args = baseline_arguments(tool)
    if base_args is None:
        return _result(
            "skip",
            "cannot synthesize baseline arguments from inputSchema; nothing to scan",
            tool.name,
            {"required": tool.required_fields},
        )

    ok, text = await call_tool_normalized(ctx.session, tool.name, base_args, ctx.call_timeout)
    if not ok:
        return _result(
            "skip",
            "baseline call failed; no response to scan for path leakage",
            tool.name,
            {"baseline_error": text[:MAX_ERROR_TEXT]},
        )

    leaks = scan_for_leaks(text)
    if leaks:
        classes = sorted({cls for cls, _ in leaks})
        examples = [masked for _, masked in leaks]
        return _result(
            "fail",
            f"{tool.name}: response leaks absolute owner filesystem path "
            f"({', '.join(classes)}; e.g. {examples[0]}) — never ship "
            "machine-local paths to clients",
            tool.name,
            {"pattern_classes": classes, "masked_examples": examples},
        )

    return _result(
        "pass",
        f"{tool.name}: baseline response free of absolute owner filesystem paths",
        tool.name,
        {},
    )


@check(id=CHECK_ID, severity=SEVERITY, citation=CITATION_HYGIENE, scope="hygiene")
async def check_path_hygiene(ctx: CheckContext) -> list[CheckResult]:
    if not ctx.tools:
        return [_result("skip", "server advertises no tools; nothing to probe", None, {})]
    return [await _probe_tool(ctx, tool) for tool in ctx.tools]
