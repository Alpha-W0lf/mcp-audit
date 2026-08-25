"""Unit tests for schema well-formedness checks (no live server needed)."""

from mcp_audit.checks.schema import check_schema_well_formed
from mcp_audit.driver import AdvertisedTool


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
    assert check_schema_well_formed(tool) == []


def test_required_field_missing_from_properties_is_caught():
    # The #4651 shape: schema drift between required list and properties.
    tool = _tool(
        {
            "type": "object",
            "properties": {"thought": {"type": "string"}},
            "required": ["thought", "nextThoughtNeeded"],
        }
    )
    problems = check_schema_well_formed(tool)
    assert any("nextThoughtNeeded" in p and "not declared" in p for p in problems)


def test_invalid_type_is_caught():
    tool = _tool(
        {
            "type": "object",
            "properties": {"thought": {"type": "strin"}},  # typo
            "required": [],
        }
    )
    problems = check_schema_well_formed(tool)
    assert any("invalid type" in p for p in problems)


def test_empty_schema_is_caught():
    problems = check_schema_well_formed(_tool({}))
    assert any("empty or missing" in p for p in problems)
