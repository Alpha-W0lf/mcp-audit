"""RUNTIME001 — advertised `required` vs runtime-enforced arguments.

The differentiating check. Seeded by modelcontextprotocol/servers#4651: a
zod refactor changed runtime validation without updating the advertised JSON
Schema, so `tools/list` and actual argument validation disagreed.

Protocol per probe-eligible tool that advertises >= 1 required field:

1. Build a baseline argument set by synthesizing values from each required
   property's declared type; call the tool with all required fields present.
   If the baseline itself fails, mark the tool skip (we cannot distinguish
   schema drift from a generally-broken tool).
2. For each required field F, call again omitting exactly F:
   - call SUCCEEDS            -> schema stricter than runtime  -> warning
                                 (harmless direction: clients sending F work)
   - error names F            -> conformant rejection          (contributes pass)
   - error names G not in
     `required`               -> runtime stricter than advertised -> error
                                 (clients following tools/list will break —
                                 the #4651 production shape)
   - unparseable/other error  -> inconclusive; recorded in details only

Safety: this check CALLS tools. It runs only on tools whose server-asserted
annotations claim readOnlyHint=true (see mcp_audit.safety), or when the
operator passes --allow-destructive. Annotations are hints, not guarantees.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from mcp import types as mcp_types

from mcp_audit.driver import AdvertisedTool
from mcp_audit.models import CheckResult
from mcp_audit.registry import CheckContext, check
from mcp_audit.safety import probe_eligibility

CITATION_4651 = "https://github.com/modelcontextprotocol/servers/issues/4651"

_SYNTHETIC_VALUES: dict[str, Any] = {
    "string": "mcp-audit-probe",
    "number": 0,
    "integer": 0,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}


def _synthesize(prop_spec: dict[str, Any]) -> Any:
    ptype = prop_spec.get("type")
    if isinstance(ptype, list):
        for candidate in ptype:
            if candidate in _SYNTHETIC_VALUES:
                return _SYNTHETIC_VALUES[candidate]
        return _SENTINEL_UNSYNTHESIZABLE
    if isinstance(ptype, str) and ptype in _SYNTHETIC_VALUES:
        return _SYNTHETIC_VALUES[ptype]
    if "default" in prop_spec:
        return prop_spec["default"]
    if (
        "enum" in prop_spec
        and isinstance(prop_spec["enum"], list)
        and prop_spec["enum"]
    ):
        return prop_spec["enum"][0]
    if "const" in prop_spec:
        return prop_spec["const"]
    if anyOf := prop_spec.get("anyOf"):
        for sub in anyOf:
            val = _synthesize(sub if isinstance(sub, dict) else {})
            if val is not _SENTINEL_UNSYNTHESIZABLE:
                return val
    return _SENTINEL_UNSYNTHESIZABLE


_SENTINEL_UNSYNTHESIZABLE = object()


def baseline_arguments(tool: AdvertisedTool) -> dict[str, Any] | None:
    """Synthesize minimal args covering every required field, or None."""
    props = tool.properties
    args: dict[str, Any] = {}
    for field_name in tool.required_fields:
        spec = props.get(field_name)
        value = _synthesize(spec if isinstance(spec, dict) else {})
        if value is _SENTINEL_UNSYNTHESIZABLE:
            return None
        args[field_name] = value
    return args


async def _call(
    session: Any, name: str, arguments: dict[str, Any], timeout: float
) -> tuple[bool, str]:
    """Call a tool; normalize protocol errors and isError results into text.

    Returns (succeeded, message_or_content_text).
    """
    try:
        result: mcp_types.CallToolResult = await asyncio.wait_for(
            session.call_tool(name, arguments=arguments), timeout=timeout
        )
    except TimeoutError:
        return False, f"probe timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001 — protocol/connection failures are probe outcomes
        return False, f"{type(e).__name__}: {e}"

    text_parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, mcp_types.TextContent):
            text_parts.append(block.text)
        else:
            text_parts.append(f"<{type(block).__name__}>")
    text = "\n".join(text_parts).strip()
    return (not result.is_error), text


def _mentioned_field(error_text: str, candidates: list[str]) -> str | None:
    """First candidate field name appearing as a word in the error text."""
    lowered = error_text.lower()
    for field_name in sorted(candidates, key=len, reverse=True):
        if re.search(
            rf"(?<![a-z0-9_]){re.escape(field_name.lower())}(?![a-z0-9_])", lowered
        ):
            return field_name
    return None


@check(id="RUNTIME001", severity="error", citation=CITATION_4651, scope="runtime")
async def check_runtime_required(ctx: CheckContext) -> list[CheckResult]:
    results: list[CheckResult] = []

    for tool in ctx.tools:
        decision = probe_eligibility(tool, allow_destructive=ctx.allow_destructive)
        if not decision.eligible:
            results.append(
                CheckResult(
                    check_id="RUNTIME001",
                    severity="error",
                    status="skip",
                    message=f"not probed: {decision.reason}",
                    citation=CITATION_4651,
                    tool_name=tool.name,
                    details={"skip_reason": decision.outcome},
                )
            )
            continue

        required = tool.required_fields
        if not required:
            results.append(
                CheckResult(
                    check_id="RUNTIME001",
                    severity="error",
                    status="pass",
                    message="no required fields advertised; nothing to probe",
                    citation=CITATION_4651,
                    tool_name=tool.name,
                    details={},
                )
            )
            continue

        base_args = baseline_arguments(tool)
        if base_args is None:
            results.append(
                CheckResult(
                    check_id="RUNTIME001",
                    severity="error",
                    status="skip",
                    message=(
                        "cannot synthesize baseline arguments from inputSchema "
                        "(unsupported property shapes); skipping probes"
                    ),
                    citation=CITATION_4651,
                    tool_name=tool.name,
                    details={"required": required},
                )
            )
            continue

        ok, msg = await _call(ctx.session, tool.name, base_args, ctx.call_timeout)
        if not ok:
            results.append(
                CheckResult(
                    check_id="RUNTIME001",
                    severity="error",
                    status="skip",
                    message=(
                        "baseline call with all required fields failed; cannot "
                        "attribute failures to individual omissions"
                    ),
                    citation=CITATION_4651,
                    tool_name=tool.name,
                    details={"baseline_error": msg[:2000]},
                )
            )
            continue

        warnings: list[str] = []
        errors: list[str] = []
        inconclusive: list[str] = []

        for omitted in required:
            probe_args = {k: v for k, v in base_args.items() if k != omitted}
            ok, msg = await _call(ctx.session, tool.name, probe_args, ctx.call_timeout)

            if ok:
                warnings.append(
                    f"omit {omitted!r}: accepted — schema stricter than runtime"
                )
                continue

            mentioned = _mentioned_field(
                msg,
                required
                + [
                    p
                    for p in tool.input_schema.get("properties", {})
                    if p not in required
                ],
            )
            if mentioned is None:
                inconclusive.append({"omitted": omitted, "error": msg[:2000]})
            elif mentioned == omitted:
                continue  # conformant rejection of an advertised-required field
            elif mentioned in required:
                # e.g. omitting A tripped validation of required B — B is
                # enforced at runtime, so still conformant for this omission,
                # but note it.
                inconclusive.append(
                    {
                        "omitted": omitted,
                        "error": msg[:2000],
                        "note": f"error names another required field {mentioned!r}",
                    }
                )
            else:
                errors.append(
                    f"omit {omitted!r}: runtime rejected undeclared-required "
                    f"field {mentioned!r} — runtime stricter than advertised"
                )

        if errors:
            status, severity, message = "fail", "error", "; ".join(errors)
        elif warnings:
            status, severity, message = "fail", "warning", "; ".join(warnings)
        else:
            status, severity, message = (
                "pass",
                "error",
                f"all {len(required)} advertised-required fields enforced at runtime",
            )

        results.append(
            CheckResult(
                check_id="RUNTIME001",
                severity=severity,
                status=status,
                message=message,
                citation=CITATION_4651,
                tool_name=tool.name,
                details={
                    "probed_required_fields": required,
                    "inconclusive": inconclusive,
                },
            )
        )

    return results
