"""ENCODING001 — multi-byte UTF-8 corruption at fixed-size chunk boundaries.

Seeded by modelcontextprotocol/servers#4666: file-reading tools that split a
file into fixed-size byte chunks and decoded each chunk *independently*
silently replaced every multi-byte UTF-8 sequence straddling a chunk boundary
with U+FFFD mojibake.

Protocol per discovered file-reading tool:

1. Write a temp fixture file whose 3-byte marker (U+20AC EURO SIGN) STARTS at
   byte offset 1022 and again at 2046 — each marker's final byte lands exactly
   ON the 1024 / 2048 boundary that a 1024-byte-chunk reader cuts at.
2. Call the tool asking for the head of the file (N=1024, then N=2048 when
   the tool advertises an integer head/count-style parameter); otherwise read
   the whole file once.
3. Fail if any response contains U+FFFD or is missing/mangling the marker;
   pass when every probe returns the marker intact.

Detection is deliberately strict: a server that truncates *before* an
incomplete trailing sequence (byte-exact slicing without replacement) also
drops the marker and is flagged. The canonical fix for #4666 — decode the
complete buffer, then slice — keeps the marker intact and passes.

Safety: read-only probes only. Every candidate goes through the shared
annotation gate (`mcp_audit.safety`); tools not asserting readOnlyHint=true
are skipped, tool-call failures degrade to skips, and this check never raises.

Discovery is intentionally conservative: a tool counts as file-reading iff
its inputSchema declares a string property whose name is (or contains as a
snake/kebab-bounded token) ``path`` / ``file`` — covering `path`,
`filepath`, `file_name`, … while rejecting `profile`, `text`, and friends.
Matching tool name/description produced too many false positives; if a
server profile ever reaches CheckContext.extra it can extend discovery
without changing callers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path

import tempfile
from typing import Any

from mcp import types as mcp_types

from mcp_audit.models import CheckResult, Status
from mcp_audit.registry import CheckContext, check
from mcp_audit.safety import probe_eligibility

CITATION_4666 = "https://github.com/modelcontextprotocol/servers/issues/4666"

CHECK_ID = "ENCODING001"
SEVERITY = "error"

# 3 bytes in UTF-8 (\xe2\x82\xac) — same corruption class as the CJK
# sequences reported in #4666.
MARKER = "\u20ac"
MARKER_BYTES = MARKER.encode("utf-8")

# Chunk sizes the probe assumes the server may cut at; the marker STARTS two
# bytes earlier so its last byte sits exactly ON the boundary.
BOUNDARIES: tuple[int, ...] = (1024, 2048)

_FIXTURE_SUFFIX = ".txt"
_TAIL_PAD_BYTES = 32

# inputSchema heuristics ------------------------------------------------------
# snake/kebab-boundary aware so "profile"/"manifesto" don't match "file".
_PATH_PROP_RE = re.compile(
    r"(?:^|[_-])(path|filepath|file_path|filename|file_name|file)(?:[_-]|$)",
    re.IGNORECASE,
)
_LIMIT_PROP_RE = re.compile(
    r"^(head|tail|lines|bytes|count|limit|length|size|"
    r"max_bytes|maxbytes|max_chars|maxchars)$",
    re.IGNORECASE,
)


def boundary_fixture(tail_pad: int = _TAIL_PAD_BYTES) -> bytes:
    """Fixture bytes with MARKER straddling every probed chunk boundary.

    Pure helper (unit-testable): the marker starts at boundary-2 for each
    BOUNDARIES entry, so decoding any aligned chunk independently replaces it
    with U+FFFD while the complete buffer stays valid UTF-8.
    """
    out = bytearray()
    for boundary in BOUNDARIES:
        start = boundary - (len(MARKER_BYTES) - 1)
        out += b"a" * (start - len(out))
        out += MARKER_BYTES
    out += b"a" * tail_pad
    return bytes(out)


def _string_properties(tool: Any) -> dict[str, Any]:
    return {
        name: spec
        for name, spec in tool.properties.items()
        if isinstance(spec, dict) and spec.get("type") == "string"
    }


def find_path_argument(tool: Any) -> str | None:
    """First string property whose name looks like a filesystem path."""
    for name in _string_properties(tool):
        if _PATH_PROP_RE.search(name):
            return name
    return None


def is_file_reader(tool: Any) -> bool:
    """Conservative discovery: has a path/file-shaped string argument."""
    return find_path_argument(tool) is not None


def find_limit_argument(tool: Any) -> str | None:
    """Optional integer head/tail/count-style parameter, if advertised."""
    for name, spec in tool.properties.items():
        if (
            isinstance(spec, dict)
            and spec.get("type") == "integer"
            and _LIMIT_PROP_RE.fullmatch(name)
        ):
            return name
    return None


def _boundary_artifacts(text: str, limit: int | None) -> list[str]:
    """Human-readable artifacts observed in one probe response."""
    artifacts: list[str] = []
    replacements = text.count("\ufffd")
    if replacements:
        artifacts.append(
            f"{replacements} U+FFFD replacement character(s) in returned content"
        )
    for boundary in BOUNDARIES:
        marker_offset = boundary - (len(MARKER_BYTES) - 1)
        if limit is not None and marker_offset >= limit:
            continue  # marker lies beyond the requested head
        if MARKER not in text:
            artifacts.append(
                f"multi-byte marker {MARKER!r} at byte offset {marker_offset} "
                f"(straddles the {boundary}-byte boundary) missing or mangled"
            )
    return artifacts


def _write_fixture(sandbox_roots: list[str] | None = None) -> str:
    """Write the boundary fixture. Prefers the server's sandbox root so the
    file sits inside the server's allowed directories; falls back to system
    temp (probes will then likely be rejected by path validation)."""
    payload = boundary_fixture()
    for root in sandbox_roots or []:
        try:
            root_path = Path(root).resolve()
            root_path.mkdir(parents=True, exist_ok=True)
            path = root_path / f"mcp-audit-enc001-{os.getpid()}{_FIXTURE_SUFFIX}"
            path.write_bytes(payload)
            return str(path)
        except OSError:
            continue
    fd, path = tempfile.mkstemp(prefix="mcp-audit-enc001-", suffix=_FIXTURE_SUFFIX)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return path




def _sandbox_roots_from_ctx(ctx) -> list[str]:
    extra = getattr(ctx, "extra", None) or {}
    raw = extra.get("sandbox_roots", extra.get("sandbox_root"))
    if raw is None:
        return []
    if isinstance(raw, (str, Path)):
        return [str(raw)]
    return [str(r) for r in raw]

async def _call_tool(
    session: Any, name: str, arguments: dict[str, Any], timeout: float
) -> tuple[bool, str]:
    """Call a tool; normalize protocol errors and isError results into text."""
    try:
        result: mcp_types.CallToolResult = await asyncio.wait_for(
            session.call_tool(name, arguments=arguments), timeout=timeout
        )
    except TimeoutError:
        return False, f"probe timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001 — protocol/connection failures are probe outcomes
        return False, f"{type(e).__name__}: {e}"

    parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        else:
            parts.append(f"<{type(block).__name__}>")
    return (not result.is_error), "\n".join(parts).strip()


def _result(
    status: Status, message: str, tool_name: str | None, details: dict[str, Any]
) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        severity=SEVERITY,
        status=status,
        message=message,
        citation=CITATION_4666,
        tool_name=tool_name,
        details=details,
    )


async def _probe_tool(ctx: CheckContext, tool: Any, fixture_path: str) -> CheckResult:
    decision = probe_eligibility(tool, allow_destructive=ctx.allow_destructive)
    if not decision.eligible:
        return _result(
            "skip",
            f"not probed: {decision.reason}",
            tool.name,
            {"skip_reason": decision.outcome},
        )

    path_arg = find_path_argument(tool)
    unsupported = [f for f in tool.required_fields if f != path_arg]
    if path_arg is None or unsupported:
        return _result(
            "skip",
            (
                f"cannot build a safe read probe: path argument "
                f"{path_arg!r} plus extra required fields {unsupported}"
                if path_arg is not None
                else "no path/file-shaped string argument found"
            ),
            tool.name,
            {"required_fields": tool.required_fields},
        )

    limit_arg = find_limit_argument(tool)
    probe_limits: tuple[int | None, ...] = (
        BOUNDARIES if limit_arg is not None else (None,)
    )

    probe_records: list[dict[str, Any]] = []
    artifacts: list[str] = []
    call_failure: str | None = None
    for limit in probe_limits:
        args: dict[str, Any] = {path_arg: fixture_path}
        if limit is not None:
            args[limit_arg] = limit
        ok, text = await _call_tool(ctx.session, tool.name, args, ctx.call_timeout)
        if not ok:
            call_failure = (
                f"probe call failed (limit={limit}): {text}"
                if limit is not None
                else f"probe call failed: {text}"
            )
            break
        found = _boundary_artifacts(text, limit)
        probe_records.append(
            {
                "arguments": args,
                "requested_boundary": limit,
                "artifacts": found,
                "response_snippet": text[:160],
            }
        )
        artifacts.extend(found)

    if call_failure is not None:
        return _result(
            "skip",
            f"not probed: {call_failure}",
            tool.name,
            {"probes": probe_records},
        )

    if artifacts:
        unique = list(dict.fromkeys(artifacts))
        return _result(
            "fail",
            f"{tool.name}: corrupt UTF-8 at chunk boundary — {'; '.join(unique)}",
            tool.name,
            {
                "probes": probe_records,
                "limit_parameter": limit_arg,
                "boundaries_probed": list(BOUNDARIES),
            },
        )

    return _result(
        "pass",
        f"{tool.name}: multi-byte marker intact across "
        f"{len(probe_records)} chunk-boundary probe(s)",
        tool.name,
        {
            "probes": probe_records,
            "limit_parameter": limit_arg,
            "boundaries_probed": list(BOUNDARIES),
        },
    )


@check(id=CHECK_ID, severity=SEVERITY, citation=CITATION_4666, scope="encoding")
async def check_encoding_chunk_boundaries(
    ctx: CheckContext,
) -> list[CheckResult]:
    candidates = [t for t in ctx.tools if is_file_reader(t)]
    if not candidates:
        return [_result("skip", "no file-reading tool to probe", None, {})]

    fixture_path = _write_fixture(_sandbox_roots_from_ctx(ctx))
    try:
        return [await _probe_tool(ctx, tool, fixture_path) for tool in candidates]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(fixture_path)
