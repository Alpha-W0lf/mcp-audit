"""RUNTIME001 — advertised `required` vs runtime-enforced arguments.

The differentiating check. Seeded by modelcontextprotocol/servers#4651: a
zod refactor changed runtime validation without updating the advertised JSON
Schema, so `tools/list` and actual argument validation disagreed.

Protocol per probe-eligible tool that advertises >= 1 required field:

1. Build a baseline argument set by synthesizing values from each required
   property's declared type; call the tool with all required fields present.
   If the baseline itself fails, mark the tool skip (we cannot distinguish
   schema drift from a generally-broken tool). String `pattern` constraints
   are honored during synthesis (see _synthesize_pattern_string): a field
   whose pattern admits no candidate is unsynthesizable, so the baseline is
   skipped rather than probing with a value the server will reject.
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
annotations claim readOnlyHint=true (see mcp_audit.safety), on tools named
via --allow-tool, or everywhere when the operator passes --allow-destructive.
Annotations are hints, not guarantees.
"""

from __future__ import annotations

import re
from typing import Any

from mcp_audit.driver import AdvertisedTool, call_tool_normalized
from mcp_audit.models import MAX_ERROR_TEXT, CheckResult, Severity, Status
from mcp_audit.registry import CheckContext, check
from mcp_audit.safety import probe_eligibility

CITATION_4651 = "https://github.com/modelcontextprotocol/servers/issues/4651"

# Recognizable synthesized string value: under --allow-destructive, a
# baseline probe on a write-shaped tool may materialize a server-side
# artifact named after it inside the server's allowed root (by design;
# operator-consented — see README).
_SYNTHESIZED_STRING = "mcp-audit-probe"


def _synthesize_number(prop_spec: dict[str, Any], is_int: bool) -> Any:
    """Numeric synthesis honoring minimum/maximum/exclusive bounds."""
    lo = prop_spec.get("minimum")
    hi = prop_spec.get("maximum")
    if lo is None:
        xlo = prop_spec.get("exclusiveMinimum")
        lo = (xlo + 1) if isinstance(xlo, (int, float)) else 1
    value = lo
    if hi is not None and value > hi:
        value = hi
        if value < lo:
            return _SENTINEL_UNSYNTHESIZABLE
    if is_int or isinstance(value, int):
        return int(value)
    return value


# Simple literal prefix after an opening `^` anchor — deliberately stops at
# the first regex metacharacter, so `^thought-` yields `thought-` but
# `^[a-z]+$` yields nothing.
_LITERAL_PREFIX_RE = re.compile(r"^\^([A-Za-z0-9][A-Za-z0-9 _-]*)")


def _literal_prefix(pattern: str) -> str | None:
    m = _LITERAL_PREFIX_RE.match(pattern)
    return m.group(1) if m else None


def _synthesize_pattern_string(
    pattern: str, field_name: str | None, min_len: Any, max_len: Any
) -> Any:
    """Synthesize a value for a regex-`pattern`-constrained string field.

    JSON Schema `pattern` is unanchored (search semantics), so candidates are
    accepted iff `re.search` matches. Candidates, in order: the field name
    itself, "mcp-audit", and a value derived from a simple literal prefix in
    the pattern (e.g. `^thought-` -> "thought-mcp-audit"). If none satisfy
    the pattern and length bounds, the field is unsynthesizable — the
    baseline is skipped rather than sending a value the server will reject.
    """
    try:
        rx = re.compile(pattern)
    except re.error:
        return _SENTINEL_UNSYNTHESIZABLE
    candidates: list[str] = []
    if field_name:
        candidates.append(field_name)
    candidates.append("mcp-audit")
    if prefix := _literal_prefix(pattern):
        candidates.append(prefix + "mcp-audit")
    for candidate in candidates:
        if len(candidate) < min_len:
            continue
        if isinstance(max_len, int) and max_len >= 0 and len(candidate) > max_len:
            continue
        if rx.search(candidate):
            return candidate
    return _SENTINEL_UNSYNTHESIZABLE


def _synthesize_string(prop_spec: dict[str, Any], field_name: str | None = None) -> Any:
    """String synthesis honoring minLength/maxLength and regex `pattern`."""
    min_len = prop_spec.get("minLength") or 0
    pattern = prop_spec.get("pattern")
    if isinstance(pattern, str) and pattern:
        return _synthesize_pattern_string(pattern, field_name, min_len, prop_spec.get("maxLength"))
    candidate = _SYNTHESIZED_STRING
    if len(candidate) < min_len:
        candidate = (candidate + "-") * (min_len // len(candidate) + 1)
        candidate = candidate[: max(min_len, 1)]
    # maxLength: truncate if the schema forbids our default length.
    max_len = prop_spec.get("maxLength")
    if isinstance(max_len, int) and max_len >= 0:
        candidate = candidate[:max_len]
        if len(candidate) < min_len:
            return _SENTINEL_UNSYNTHESIZABLE
    return candidate


def _synthesize(prop_spec: dict[str, Any], field_name: str | None = None) -> Any:
    if not isinstance(prop_spec, dict):
        return _SENTINEL_UNSYNTHESIZABLE
    if "default" in prop_spec:
        return prop_spec["default"]
    if "const" in prop_spec:
        return prop_spec["const"]
    if "enum" in prop_spec and isinstance(prop_spec["enum"], list) and prop_spec["enum"]:
        return prop_spec["enum"][0]
    if anyOf := prop_spec.get("anyOf"):
        for sub in anyOf:
            val = _synthesize(sub if isinstance(sub, dict) else {})
            if val is not _SENTINEL_UNSYNTHESIZABLE:
                return val
        return _SENTINEL_UNSYNTHESIZABLE
    ptype = prop_spec.get("type")
    if isinstance(ptype, list):
        for candidate in ptype:
            val = _synthesize({**prop_spec, "type": candidate})
            if val is not _SENTINEL_UNSYNTHESIZABLE:
                return val
        return _SENTINEL_UNSYNTHESIZABLE
    if ptype in ("integer", "number"):
        return _synthesize_number(prop_spec, ptype == "integer")
    if ptype == "boolean":
        return True
    if ptype == "string":
        return _synthesize_string(prop_spec, field_name=field_name)
    if ptype == "array":
        min_items = prop_spec.get("minItems") or 0
        if min_items == 0:
            return []
        item_spec = prop_spec.get("items") or {}
        val = _synthesize(item_spec if isinstance(item_spec, dict) else {})
        return [val] if val is not _SENTINEL_UNSYNTHESIZABLE else _SENTINEL_UNSYNTHESIZABLE
    if ptype == "object":
        sub_props = prop_spec.get("properties", {})
        sub_required = prop_spec.get("required", [])
        if not sub_required:
            return {}
        out = {}
        for field in sub_required:
            val = _synthesize(sub_props.get(field, {}))
            if val is _SENTINEL_UNSYNTHESIZABLE:
                return _SENTINEL_UNSYNTHESIZABLE
            out[field] = val
        return out
    return _SENTINEL_UNSYNTHESIZABLE


_SENTINEL_UNSYNTHESIZABLE = object()


def baseline_arguments(tool: AdvertisedTool) -> dict[str, Any] | None:
    """Synthesize minimal args covering every required field, or None."""
    props = tool.properties
    args: dict[str, Any] = {}
    for field_name in tool.required_fields:
        spec = props.get(field_name)
        value = _synthesize(spec if isinstance(spec, dict) else {}, field_name=field_name)
        if value is _SENTINEL_UNSYNTHESIZABLE:
            return None
        args[field_name] = value
    return args


def _mentioned_field(error_text: str, candidates: list[str]) -> str | None:
    """First candidate field name appearing as a word in the error text."""
    lowered = error_text.lower()
    for field_name in sorted(candidates, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9_]){re.escape(field_name.lower())}(?![a-z0-9_])", lowered):
            return field_name
    return None


@check(id="RUNTIME001", severity="error", citation=CITATION_4651, scope="runtime")
async def check_runtime_required(ctx: CheckContext) -> list[CheckResult]:
    if not ctx.tools:
        return [
            CheckResult(
                check_id="RUNTIME001",
                severity="error",
                status="skip",
                message="server advertises no tools; nothing to probe",
                citation=CITATION_4651,
                details={},
            )
        ]

    results: list[CheckResult] = []

    for tool in ctx.tools:
        decision = probe_eligibility(
            tool, allow_destructive=ctx.allow_destructive, allow_tools=ctx.allow_tools
        )
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

        ok, msg = await call_tool_normalized(ctx.session, tool.name, base_args, ctx.call_timeout)
        if not ok:
            # Baseline failure is itself diagnostic when the error names a
            # field the schema does NOT advertise as required (#4651 class:
            # runtime enforces more than tools/list advertises).
            unadvertised = [
                name
                for name in tool.properties
                if name not in tool.required_fields and _mentioned_field(msg, [name])
            ]
            if unadvertised:
                results.append(
                    CheckResult(
                        check_id="RUNTIME001",
                        severity="error",
                        status="fail",
                        message=(
                            f"runtime validation requires {unadvertised!r} but "
                            f"inputSchema.required omits them (advertised required: "
                            f"{tool.required_fields!r}) — schema/runtime mismatch"
                        ),
                        citation=CITATION_4651,
                        tool_name=tool.name,
                        details={"baseline_error": msg[:MAX_ERROR_TEXT]},
                    )
                )
            else:
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
                        details={"baseline_error": msg[:MAX_ERROR_TEXT]},
                    )
                )
            continue

        warnings: list[str] = []
        errors: list[str] = []
        inconclusive: list[dict[str, Any]] = []

        for omitted in required:
            probe_args = {k: v for k, v in base_args.items() if k != omitted}
            ok, msg = await call_tool_normalized(
                ctx.session, tool.name, probe_args, ctx.call_timeout
            )

            if ok:
                warnings.append(f"omit {omitted!r}: accepted — schema stricter than runtime")
                continue

            mentioned = _mentioned_field(
                msg,
                required
                + [p for p in tool.input_schema.get("properties", {}) if p not in required],
            )
            if mentioned is None:
                inconclusive.append({"omitted": omitted, "error": msg[:MAX_ERROR_TEXT]})
            elif mentioned == omitted:
                continue  # conformant rejection of an advertised-required field
            elif mentioned in required:
                # e.g. omitting A tripped validation of required B — B is
                # enforced at runtime, so still conformant for this omission,
                # but note it.
                inconclusive.append(
                    {
                        "omitted": omitted,
                        "error": msg[:MAX_ERROR_TEXT],
                        "note": f"error names another required field {mentioned!r}",
                    }
                )
            else:
                errors.append(
                    f"omit {omitted!r}: runtime rejected undeclared-required "
                    f"field {mentioned!r} — runtime stricter than advertised"
                )

        status: Status
        severity: Severity
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
