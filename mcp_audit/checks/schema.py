"""Schema-compliance checks.

Seeded by modelcontextprotocol/servers#4651: a tool's `inputSchema.required`
advertised one contract while runtime validation enforced another. These
checks validate the advertised schema's internal consistency — the failure
mode that ships when codegen or preprocessing layers silently change what a
schema says.
"""

from __future__ import annotations

from typing import Any

from mcp_audit.driver import AdvertisedTool

VALID_TYPES = {
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "null",
}


def check_schema_well_formed(tool: AdvertisedTool) -> list[str]:
    """Return a list of problems with the tool's advertised input schema.

    Empty list = no problems found. Checks:
    - required fields must exist in properties (#4651 class: required naming
      fields the schema never defines)
    - property types must be valid JSON-Schema types
    - object properties must themselves declare a type
    """
    problems: list[str] = []
    schema: dict[str, Any] = tool.input_schema

    if not schema:
        problems.append(f"{tool.name}: inputSchema is empty or missing")
        return problems

    if schema.get("type") != "object":
        problems.append(
            f"{tool.name}: inputSchema.type should be 'object', got {schema.get('type')!r}"
        )

    props: dict[str, Any] = schema.get("properties", {})
    required: list[Any] = schema.get("required", [])

    for field in required:
        if field not in props:
            problems.append(
                f"{tool.name}: required field {field!r} is not declared in properties "
                f"(advertised required: {required!r}; properties: {sorted(props)!r})"
            )

    for name, spec in props.items():
        if not isinstance(spec, dict):
            problems.append(f"{tool.name}.{name}: property spec is not an object")
            continue
        ptype = spec.get("type")
        if ptype is None and "anyOf" not in spec and "$ref" not in spec:
            problems.append(f"{tool.name}.{name}: property has no type (and no anyOf/$ref)")
        elif isinstance(ptype, str) and ptype not in VALID_TYPES:
            problems.append(f"{tool.name}.{name}: invalid type {ptype!r}")

    return problems


def check_required_matches_runtime_probe(
    tool: AdvertisedTool,
    call_tool,  # async callable(name, arguments) -> result
    omit_field: str,
) -> str | None:
    """Runtime probe (opt-in): omit one advertised-required field and call.

    Returns a problem description if the runtime accepts the call despite the
    field being advertised as required (schema says required, runtime doesn't
    care — the #4651 mismatch in the other direction), or if the runtime
    rejects a field the schema never required.

    ONLY use against tools confirmed side-effect-free, or with a sandboxed
    server instance. This is why it is a separate opt-in function, not part
    of check_schema_well_formed.
    """
    raise NotImplementedError(
        "Runtime probing requires per-server side-effect profiles; "
        "planned for v0.2. See README principles."
    )
