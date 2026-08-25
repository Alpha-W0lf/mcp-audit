"""SCHEMA001 — advertised inputSchema internal consistency.

Refactor of v0.1's check_schema_well_formed onto the registry/CheckResult
model. Seeded by modelcontextprotocol/servers#4651: a tool's
`inputSchema.required` advertised one contract while runtime validation
enforced another, after a zod refactor changed schema generation.

Static scope: no tool calls are made.
"""

from __future__ import annotations

from typing import Any

from mcp_audit.models import CheckResult
from mcp_audit.registry import CheckContext, check

CITATION_4651 = "https://github.com/modelcontextprotocol/servers/issues/4651"

VALID_TYPES = {
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "null",
}


def schema_problems(tool_name: str, schema: dict[str, Any]) -> list[str]:
    """Pure helper (kept from v0.1 for unit-testability): list of problems."""
    problems: list[str] = []

    if not schema:
        return [f"{tool_name}: inputSchema is empty or missing"]

    if schema.get("type") != "object":
        problems.append(
            f"{tool_name}: inputSchema.type should be 'object', got {schema.get('type')!r}"
        )

    props: dict[str, Any] = schema.get("properties", {})
    required: list[Any] = schema.get("required", [])

    for field_name in required:
        if field_name not in props:
            problems.append(
                f"{tool_name}: required field {field_name!r} is not declared in properties "
                f"(advertised required: {required!r}; properties: {sorted(props)!r})"
            )

    for name, spec in props.items():
        if not isinstance(spec, dict):
            problems.append(f"{tool_name}.{name}: property spec is not an object")
            continue
        ptype = spec.get("type")
        if ptype is None and "anyOf" not in spec and "$ref" not in spec:
            problems.append(
                f"{tool_name}.{name}: property has no type (and no anyOf/$ref)"
            )
        elif isinstance(ptype, str) and ptype not in VALID_TYPES:
            problems.append(f"{tool_name}.{name}: invalid type {ptype!r}")

    return problems


@check(id="SCHEMA001", severity="error", citation=CITATION_4651, scope="schema")
async def check_schema_well_formed(ctx: CheckContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for tool in ctx.tools:
        problems = schema_problems(tool.name, tool.input_schema)
        results.append(
            CheckResult(
                check_id="SCHEMA001",
                severity="error",
                status="pass" if not problems else "fail",
                message=(
                    "inputSchema internally consistent"
                    if not problems
                    else "; ".join(problems)
                ),
                citation=CITATION_4651,
                tool_name=tool.name,
                details={"problems": problems},
            )
        )
    return results
