"""ENCODING001 — multi-byte UTF-8 corruption at fixed-size chunk boundaries.

Seeded by public issue modelcontextprotocol/servers#4666 filed by others:
file-reading tools that split a file into fixed-size byte chunks and decoded each chunk
*independently* silently replaced every multi-byte UTF-8 sequence straddling a chunk boundary
with U+FFFD mojibake. This check reproduces that failure class.

Protocol per discovered file-reading tool:

1. Write a temp fixture file whose 3-byte marker (U+20AC EURO SIGN) STARTS at
   byte offset 1022 and again at 2046 — each marker's final byte lands exactly
   ON the 1024 / 2048 boundary that a 1024-byte-chunk reader cuts at.
2. Call the tool asking for the head of the file (N=1024, then N=2048 when
   the tool advertises an integer head/count-style parameter); otherwise read
   the whole file once.
3. Fail if any response contains U+FFFD (a true corruption signal), or when
   the response demonstrably contains fixture text but the marker is
   missing/mangled; pass when every fixture-bearing probe returns the marker
   intact; skip when no response contains fixture text.

Fixture-text heuristic: the fixture payload is mostly ``b"a"`` padding, so a
run of 64+ consecutive 'a' characters in a response proves the server
returned fixture text — and a missing marker there proves decoding loss.
Marker ABSENCE in a response without that padding run proves nothing:
metadata tools (e.g. get_file_info) return file stats rather than content,
and binary readers (e.g. read_media_file) return base64/embedded-resource
blobs that were never decoded. Those are not decoding surfaces, so the check
skips instead of failing them.

Detection is deliberately strict where it applies: a server that truncates
*before* an incomplete trailing sequence (byte-exact slicing without
replacement) also drops the marker and is flagged. The canonical fix for
#4666 — decode the complete buffer, then slice — keeps the marker intact and
passes.

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

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from mcp_audit.driver import call_tool_normalized
from mcp_audit.models import CheckResult, Severity, Status
from mcp_audit.registry import CheckContext, check, sandbox_roots_from_extra
from mcp_audit.safety import probe_eligibility

CITATION_4666 = "https://github.com/modelcontextprotocol/servers/issues/4666"

CHECK_ID = "ENCODING001"
SEVERITY: Severity = "error"

# 3 bytes in UTF-8 (\xe2\x82\xac) — same corruption class as the CJK
# sequences reported in #4666.
MARKER = "\u20ac"
MARKER_BYTES = MARKER.encode("utf-8")

# Chunk sizes the probe assumes the server may cut at; the marker STARTS two
# bytes earlier so its last byte sits exactly ON the boundary.
BOUNDARIES: tuple[int, ...] = (1024, 2048)

_FIXTURE_SUFFIX = ".txt"
_TAIL_PAD_BYTES = 32

# Fixture-text heuristic (see module docstring): the fixture payload is
# mostly `b"a"` padding, so a run of this many consecutive 'a' characters
# proves the response contains fixture text.
_PADDING_RUN_MIN = 64
_PADDING_RUN_RE = re.compile(f"a{{{_PADDING_RUN_MIN},}}")

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


def _contains_fixture_text(text: str) -> bool:
    """True iff the response demonstrably contains the fixture payload."""
    return bool(_PADDING_RUN_RE.search(text))


def _boundary_artifacts(text: str, limit: int | None) -> tuple[list[str], bool]:
    """Artifacts observed in one probe response, plus whether the response
    demonstrably contains fixture text (the padding-run heuristic).

    U+FFFD is reported unconditionally — it is a true corruption signal in
    any text payload. A missing/mangled marker is reported here too, but the
    caller only fails on it when fixture text is present; metadata or
    non-text responses (file stats, base64/embedded-resource blobs) never
    contained the marker to begin with.
    """
    artifacts: list[str] = []
    saw_fixture_text = _contains_fixture_text(text)
    replacements = text.count("\ufffd")
    if replacements:
        artifacts.append(f"{replacements} U+FFFD replacement character(s) in returned content")
    for boundary in BOUNDARIES:
        marker_offset = boundary - (len(MARKER_BYTES) - 1)
        if limit is not None and marker_offset >= limit:
            continue  # marker lies beyond the requested head
        if MARKER not in text:
            artifacts.append(
                f"multi-byte marker {MARKER!r} at byte offset {marker_offset} "
                f"(straddles the {boundary}-byte boundary) missing or mangled"
            )
    return artifacts, saw_fixture_text


def _write_fixture(sandbox_roots: list[Path] | None = None) -> str:
    """Write the boundary fixture. Prefers the server's sandbox root so the
    file sits inside the server's allowed directories; falls back to system
    temp (probes will then likely be rejected by path validation).

    The audit tool NEVER creates directories: sandbox roots are pre-filtered
    to existing directories by mcp_audit.registry.sandbox_roots_from_extra.
    Fixture names come from tempfile.mkstemp — unpredictable, so a hostile
    server cannot pre-place a symlink at a guessed path and redirect the
    write.
    """
    payload = boundary_fixture()
    dirs = [Path(root) for root in sandbox_roots or []]
    dirs.append(Path(tempfile.gettempdir()))
    for directory in dirs:
        try:
            fd, path = tempfile.mkstemp(
                dir=directory, prefix="mcp-audit-enc001-", suffix=_FIXTURE_SUFFIX
            )
        except OSError:
            continue
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return path
    raise OSError("could not write the ENCODING001 probe fixture anywhere")


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
    probe_limits: tuple[int | None, ...] = BOUNDARIES if limit_arg is not None else (None,)

    probe_records: list[dict[str, Any]] = []
    artifacts: list[str] = []
    saw_fixture_text = False
    call_failure: str | None = None
    for limit in probe_limits:
        args: dict[str, Any] = {path_arg: fixture_path}
        if limit is not None and limit_arg is not None:
            args[limit_arg] = limit
        ok, text = await call_tool_normalized(ctx.session, tool.name, args, ctx.call_timeout)
        if not ok:
            call_failure = (
                f"probe call failed (limit={limit}): {text}"
                if limit is not None
                else f"probe call failed: {text}"
            )
            break
        found, fixture_text = _boundary_artifacts(text, limit)
        saw_fixture_text = saw_fixture_text or fixture_text
        probe_records.append(
            {
                "arguments": args,
                "requested_boundary": limit,
                "artifacts": found,
                "contains_fixture_text": fixture_text,
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

    if artifacts and not saw_fixture_text:
        # Marker absence proves nothing when the response never carried
        # fixture text: metadata replies, base64/embedded-resource blobs, and
        # short/empty responses are not decoding surfaces.
        return _result(
            "skip",
            f"{tool.name}: response does not contain fixture text "
            "(metadata or non-text content) — not a decoding surface",
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

    fixture_path = _write_fixture(sandbox_roots_from_extra(ctx.extra))
    try:
        return [await _probe_tool(ctx, tool, fixture_path) for tool in candidates]
    finally:
        with contextlib.suppress(OSError):
            os.unlink(fixture_path)
