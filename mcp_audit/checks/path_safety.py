"""PATHSAFE001 — Windows drive-letter paths must be rejected on POSIX hosts.

Seeded by public issue modelcontextprotocol/servers#4686 filed by others: on a POSIX host,
a filesystem tool accepted `C:\\Users\\me\\file.md`, treated it as a *relative* path
(backslash is an ordinary filename character on POSIX), and created a single
literal backslash filename inside its sandbox root instead of rejecting an
out-of-root absolute path. This check reproduces that failure class.

Tool-scope heuristic (documented, deliberately narrow)
-----------------------------------------------------
A tool is path-accepting when its inputSchema declares a **string** property
whose name tokenizes (snake/kebab/camelCase aware) to a filesystem-path
vocabulary token: {path, file, dir, directory, folder}, plus joined forms
{filepath, filename, dirname, pathname}. Tokenization prevents false hits
like `profile` (contains "file" as a substring but is not a path). The
first matching property wins with priority: exact `path`, then common
spelling variants, then anything else alphabetically.

Probe protocol (single probe, POSIX hosts only)
-----------------------------------------------
1. Pick the FIRST tool that is both path-accepting and probe-eligible per
   `mcp_audit.safety.probe_eligibility`. Among equally eligible candidates,
   tools whose own annotations assert write capability (destructiveHint=true
   or readOnlyHint=false) are preferred for the single-probe budget — the
   #4686 class lives in filesystem *writers*, and the operator has already
   accepted writes by passing --allow-destructive. Remaining candidates get
   an explanatory skip (no per-tool spam).
2. Because this probe WRITES, it additionally requires the operator's
   destructive allowance (`--allow-destructive`) even for tools claiming
   `readOnlyHint=true` — the kit's read-only-by-default posture forbids
   implicit writes.
3. Call the tool with benign synthesized arguments (same synthesis rules as
   RUNTIME001) except the path property = `C:\\mcp-audit-probe.txt`, a
   drive-letter form pointing OUTSIDE any allowed root.
4. Outcomes:
   - error result whose text hits rejection vocabulary AND no literal
     backslash file appeared in the known sandbox roots -> PASS (server
     validates). Merely echoing the probe value in an unrelated error is
     NOT accepted as a rejection — that shape overclaims. A conformant
     server may reject at argument validation rather than filesystem path
     validation; both produce no literal file, and the vocabulary
     requirement keeps the PASS attributable to path handling, so the
     corroboration still holds.
   - any other error (or an unverifiable "rejection")      -> SKIP
     (cannot attribute the failure to path handling);
   - success                                                -> FAIL (#4686
     shape). When sandbox roots are supplied via
     ``ctx.extra["sandbox_roots"]``, the check corroborates the bug by
     locating the literal backslash filename there, records its absolute
     path in details, and DELETES it (cleanup); without a known root,
     acceptance alone is still reported FAIL.

Non-POSIX hosts (`os.name != "posix"`) skip entirely: drive-letter input is
native there, so acceptance is correct behavior.

Scope note: plain-backslash relative names (`dir\\file.txt`) are OUT of
scope by design — on POSIX they are legitimate single filenames *inside*
the sandbox, so creating them is unusual naming, not the sandbox-escape
bug class #4686 covers. UNC paths (`\\\\server\\share`) are likewise a
separate planned assertion.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mcp_audit.checks.runtime_required import baseline_arguments
from mcp_audit.driver import AdvertisedTool, call_tool_normalized
from mcp_audit.models import MAX_ERROR_TEXT, CheckResult, Status
from mcp_audit.registry import CheckContext, check, sandbox_roots_from_extra
from mcp_audit.safety import probe_eligibility

CITATION_4686 = "https://github.com/modelcontextprotocol/servers/issues/4686"

CHECK_ID = "PATHSAFE001"

PROBE_VALUE = "C:\\mcp-audit-probe.txt"

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:(?:[\\/].*)?$")

_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")

_PATH_TOKENS = frozenset({"path", "file", "dir", "directory", "folder"})
_JOINED_NAMES = frozenset({"filepath", "filename", "dirname", "pathname"})

_REJECTION_VOCAB = (
    "invalid",
    "illegal",
    "absolute",
    "drive",
    "windows",
    "posix",
    "relative",
    "traversal",
    "escape",
    "not allowed",
    "not permitted",
    "rejected",
    "denied",
    "unsupported",
    "outside",
)


def is_drive_letter_path(value: Any) -> bool:
    """True iff value is a Windows drive-letter path form.

    Accepts `C:\\x`, `Z:/x`, bare `C:` (case-insensitive single ASCII letter
    followed by a colon, optionally followed by a slash/backslash and more).
    Rejects everything else, including bare `C:x` (drive-relative without a
    separator — ambiguous and outside the documented heuristic).
    """
    return isinstance(value, str) and bool(_DRIVE_LETTER_RE.match(value))


def _name_tokens(name: str) -> set[str]:
    joined = re.sub(r"[\s_\-]+", "", name)
    return {t.lower() for t in _TOKEN_RE.findall(joined)}


def _is_path_like_name(name: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]", "", name.lower())
    if lowered in _JOINED_NAMES:
        return True
    return bool(_name_tokens(name) & _PATH_TOKENS)


def _priority(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if lowered == "path":
        return (0, name)
    if lowered in {"file_path", "filepath", "filename", "file", "pathname"}:
        return (1, name)
    return (2, name)


def path_like_properties(tool: AdvertisedTool) -> list[str]:
    """String-typed properties whose names look like filesystem paths."""
    matches: list[str] = []
    for prop_name, spec in tool.properties.items():
        if not isinstance(spec, dict):
            continue
        ptype = spec.get("type")
        is_string = ptype == "string" or (isinstance(ptype, list) and "string" in ptype)
        if is_string and _is_path_like_name(prop_name):
            matches.append(prop_name)
    return sorted(matches, key=_priority)


def _find_literal_file(roots: list[Path], literal_name: str) -> Path | None:
    for root in roots:
        candidate = root / literal_name
        if candidate.is_file():
            return candidate
    return None


def _looks_like_path_rejection(error_text: str) -> bool:
    """True iff the error text uses path-rejection vocabulary.

    Deliberately does NOT treat the probe value appearing anywhere in the
    message as a rejection: a server erroring for unrelated reasons while
    echoing the path is inconclusive, not conformant.
    """
    lowered = error_text.lower()
    return any(term in lowered for term in _REJECTION_VOCAB)


def _result(status: Status, message: str, *, tool_name: str | None, **details) -> CheckResult:
    return CheckResult(
        check_id=CHECK_ID,
        severity="error",
        status=status,
        message=message,
        citation=CITATION_4686,
        tool_name=tool_name,
        details=details,
    )


def _prefers_writer(tool: AdvertisedTool) -> int:
    ann = tool.annotations or {}
    writes_asserted = ann.get("destructiveHint") is True or ann.get("readOnlyHint") is False
    return 0 if writes_asserted else 1


@check(id=CHECK_ID, severity="error", citation=CITATION_4686, scope="path_safety")
async def check_path_safety(ctx: CheckContext) -> list[CheckResult]:
    if os.name != "posix":
        return [
            _result(
                "skip",
                "non-POSIX host: drive-letter paths are native here, nothing to assert",
                tool_name=None,
                host_os=os.name,
            )
        ]

    results: list[CheckResult] = []
    eligible: list[tuple[AdvertisedTool, str]] = []

    for tool in ctx.tools:
        matches = path_like_properties(tool)
        if not matches:
            continue
        decision = probe_eligibility(
            tool, allow_destructive=ctx.allow_destructive, allow_tools=ctx.allow_tools
        )
        if not decision.eligible:
            results.append(
                _result(
                    "skip",
                    f"not probed: {decision.reason}",
                    tool_name=tool.name,
                    skip_reason=decision.outcome,
                )
            )
        elif not ctx.allow_destructive:
            results.append(
                _result(
                    "skip",
                    "not probed: this check performs a write probe, which "
                    "requires explicit --allow-destructive",
                    tool_name=tool.name,
                    skip_reason="write_probe_requires_allow_destructive",
                )
            )
        else:
            eligible.append((tool, matches[0]))

    if not results and not eligible:
        return [
            _result(
                "skip",
                "no tools advertise a string property named like a "
                "filesystem path; nothing to probe",
                tool_name=None,
            )
        ]

    if not eligible:
        return results

    eligible.sort(key=lambda pair: _prefers_writer(pair[0]))
    probed, target_prop = eligible[0]
    for tool, _prop in eligible[1:]:
        results.append(
            _result(
                "skip",
                "not probed: single-probe policy — only the first eligible "
                f"path tool ({probed.name!r}) is probed",
                tool_name=tool.name,
                skip_reason="first_eligible_only",
            )
        )

    base_args = baseline_arguments(probed)
    if base_args is None:
        results.append(
            _result(
                "skip",
                "cannot synthesize arguments from inputSchema; probe aborted",
                tool_name=probed.name,
                skip_reason="unsynthesizable_schema",
            )
        )
        return results

    args = dict(base_args)
    args[target_prop] = PROBE_VALUE

    ok, msg = await call_tool_normalized(ctx.session, probed.name, args, ctx.call_timeout)

    if not ok:
        # Corroborate before scoring PASS: the "rejection" only counts when
        # the server also did NOT materialize the probe path as a literal
        # file in a known sandbox root. (An error echoing the probe value
        # while still writing the file is an overclaim trap.)
        roots = sandbox_roots_from_extra(ctx.extra)
        literal = _find_literal_file(roots, PROBE_VALUE)
        cleanup: str | None = None
        if literal is not None:
            try:
                literal.unlink()
                cleanup = "removed"
            except OSError as e:
                cleanup = f"failed: {type(e).__name__}: {e}"
        if _looks_like_path_rejection(msg) and literal is None:
            results.append(
                _result(
                    "pass",
                    "rejected Windows drive-letter path outside allowed "
                    "roots (conformant validation)",
                    tool_name=probed.name,
                    probe_value=PROBE_VALUE,
                    error=msg[:MAX_ERROR_TEXT],
                )
            )
        else:
            reason = (
                "error reads as a path rejection but the probe file was "
                "created in a sandbox root anyway; outcome inconclusive"
                if literal is not None
                else "tool errored for reasons unrelated to path validation; outcome inconclusive"
            )
            results.append(
                _result(
                    "skip",
                    reason,
                    tool_name=probed.name,
                    skip_reason=(
                        "rejection_not_corroborated"
                        if literal is not None
                        else "unattributable_error"
                    ),
                    error=msg[:MAX_ERROR_TEXT],
                    **({"cleanup": cleanup} if cleanup is not None else {}),
                )
            )
        return results

    roots = sandbox_roots_from_extra(ctx.extra)
    literal = _find_literal_file(roots, PROBE_VALUE)
    details: dict[str, Any] = {
        "probe_value": PROBE_VALUE,
        "path_property": target_prop,
        "result_text": msg[:MAX_ERROR_TEXT],
        "corroborated": literal is not None,
    }
    if literal is None:
        details["note"] = (
            "sandbox root unknown or literal file not found there; "
            "acceptance of an out-of-root drive-letter path is itself "
            "non-conformant"
        )
    message = f"accepted Windows drive-letter path {PROBE_VALUE!r} on POSIX"
    if literal is not None:
        details["literal_file_created"] = str(literal)
        message += f" and created a literal backslash filename inside the sandbox root ({literal})"
        try:
            literal.unlink()
            details["cleanup"] = "removed"
        except OSError as e:
            details["cleanup"] = f"failed: {type(e).__name__}: {e}"
    results.append(
        _result(
            "fail",
            message + " — see modelcontextprotocol/servers#4686",
            tool_name=probed.name,
            **details,
        )
    )
    return results
