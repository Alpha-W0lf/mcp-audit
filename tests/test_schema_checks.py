"""Unit tests for SCHEMA001 (no live server needed).

Adapted from v0.1: the check now returns CheckResult objects via the registry
instead of bare problem strings.
"""

import pytest

from mcp_audit.checks.schema import schema_problems
from mcp_audit.driver import AdvertisedTool
from mcp_audit.registry import REGISTRY, CheckContext


def _tool(schema: dict) -> AdvertisedTool:
    return AdvertisedTool(name="think", description=None, input_schema=schema)


def test_valid_schema_passes():
    tool = _tool(
        {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "nextThoughtNeeded": {"type": "boolean"},
            },
            "required": ["thought", "nextThoughtNeeded"],
        }
    )
    assert schema_problems(tool.name, tool.input_schema) == []


def test_required_field_missing_from_properties_is_caught():
    # Static well-formedness: advertised required names a field that is not in
    # properties. This is NOT the #4651 production shape (runtime stricter
    # than advertised required; that is RUNTIME001). Flags static declaration
    # drift before runtime probing.
    tool = _tool(
        {
            "type": "object",
            "properties": {"thought": {"type": "string"}},
            "required": ["thought", "nextThoughtNeeded"],
        }
    )
    problems = schema_problems(tool.name, tool.input_schema)
    assert any("nextThoughtNeeded" in p and "not declared" in p for p in problems)


def test_invalid_type_is_caught():
    tool = _tool(
        {
            "type": "object",
            "properties": {"thought": {"type": "strin"}},  # typo
            "required": [],
        }
    )
    problems = schema_problems(tool.name, tool.input_schema)
    assert any("invalid type" in p for p in problems)


def test_empty_schema_is_caught():
    problems = schema_problems("think", {})
    assert any("empty or missing" in p for p in problems)


# --- registry-integrated behavior -------------------------------------------


@pytest.mark.asyncio
async def test_check_registered_and_emits_checkresults():
    spec = REGISTRY.get("SCHEMA001")
    assert spec.severity == "error"
    assert spec.citation and "4651" in spec.citation

    good = AdvertisedTool(
        name="ok",
        description=None,
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
    )
    bad = _tool({})
    ctx = CheckContext(session=None, tools=[good, bad])
    results = await spec.fn(ctx)
    by_tool = {r.tool_name: r for r in results}
    assert by_tool["ok"].status == "pass"
    assert by_tool["think"].status == "fail"
    assert by_tool["think"].check_id == "SCHEMA001"
