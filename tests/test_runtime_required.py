"""Unit tests for RUNTIME001 probe logic — fake session, no real server.

The fake session replays canned CallToolResults keyed by which arguments are
present, letting us exercise every branch: conformant rejection, loose schema
(warning), hidden runtime requirement (error), baseline failure, unsynthesizable
schemas, and the annotation skip gate.
"""

import pytest
from mcp import types

from mcp_audit.checks.runtime_required import baseline_arguments
from mcp_audit.driver import AdvertisedTool
from mcp_audit.registry import REGISTRY, CheckContext, load_checks


class FakeSession:
    """Returns error iff a predicate over the arguments says so."""

    def __init__(self, behavior):
        self.behavior = behavior  # fn(arguments) -> (ok: bool, text: str)

    async def call_tool(self, name, arguments=None):
        ok, text = self.behavior(dict(arguments or {}))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=not ok
        )


def _tool(required, properties=None) -> AdvertisedTool:
    props = properties or {f: {"type": "string"} for f in required}
    return AdvertisedTool(
        name="probe_me",
        description=None,
        input_schema={"type": "object", "properties": props, "required": required},
        annotations={"readOnlyHint": True},
    )


def _ctx(session, tools, **kw):
    return CheckContext(session=session, tools=tools, **kw)


def _result_for(results, tool_name="probe_me") -> object:
    return next(r for r in results if r.tool_name == tool_name)


@pytest.fixture(autouse=True)
def _load():
    load_checks()


# --- pure helpers ------------------------------------------------------------


def test_baseline_synthesis():
    assert baseline_arguments(_tool(["a"])) == {"a": "mcp-audit"}
    assert baseline_arguments(_tool(["n"], {"n": {"type": "integer"}})) == {"n": 1}
    # Constraint-aware: minimum/maxLength/minLength are honored (the official
    # sequentialthinking server requires thoughtNumber >= 1 — synthesis of 0
    # made the baseline fail and masked the #4651 mismatch).
    assert baseline_arguments(
        _tool(["n"], {"n": {"type": "integer", "minimum": 5}})
    ) == {"n": 5}
    assert baseline_arguments(
        _tool(["b"], {"b": {"type": "boolean"}})
    ) == {"b": True}
    assert baseline_arguments(
        _tool(["s"], {"s": {"type": "string", "minLength": 20}})
    ) == {"a": None} or baseline_arguments(
        _tool(["s"], {"s": {"type": "string", "minLength": 20}})
    )["s"].startswith("mcp-audit-")


def test_baseline_unsynthesizable_returns_none():
    t = _tool(["mystery"], {"mystery": {}})
    assert baseline_arguments(t) is None


# --- check branches ----------------------------------------------------------


@pytest.mark.asyncio
async def test_conformant_rejection_passes():
    # omitting advertised-required field is rejected, error names that field
    session = FakeSession(
        lambda args: (
            ("text" in args),
            "missing required parameter 'text'" if "text" not in args else "ok",
        )
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [_tool(["text"])]))
    r = _result_for(results)
    assert r.status == "pass" and r.severity == "error"


@pytest.mark.asyncio
async def test_loose_schema_warns():
    # call succeeds even though advertised-required field omitted -> warning
    session = FakeSession(lambda args: (True, "processed"))
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [_tool(["must"])]))
    r = _result_for(results)
    assert r.status == "fail"
    assert r.severity == "warning"
    assert "schema stricter than runtime" in r.message


@pytest.mark.asyncio
async def test_hidden_requirement_errors():
    # omitting 'alpha' trips validation of undeclared-required 'beta'
    def behavior(args):
        if "beta" not in args and "alpha" not in args:
            return False, "Invalid arguments: missing required parameter 'beta'"
        if "alpha" not in args:
            return False, "Invalid arguments: missing required parameter 'alpha'"
        return True, "echoed"

    session = FakeSession(behavior)
    t = _tool(["alpha"], {"alpha": {"type": "string"}, "beta": {"type": "string"}})
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    r = _result_for(results)
    assert r.status == "fail" and r.severity == "error"
    assert "runtime stricter than advertised" in r.message


@pytest.mark.asyncio
async def test_baseline_failure_skips_tool():
    session = FakeSession(lambda args: (False, "server on fire"))
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [_tool(["x"])]))
    r = _result_for(results)
    assert r.status == "skip"
    assert "baseline" in r.message


@pytest.mark.asyncio
async def test_unsynthesizable_schema_skips():
    session = FakeSession(lambda args: (True, "unused"))
    t = _tool(["mystery"], {"mystery": {}})
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    r = _result_for(results)
    assert r.status == "skip"


@pytest.mark.asyncio
async def test_no_required_fields_is_trivial_pass():
    session = FakeSession(lambda args: (True, ""))
    t = AdvertisedTool(
        name="noargs",
        description=None,
        input_schema={"type": "object", "properties": {}},
        annotations={"readOnlyHint": True},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    r = _result_for(results, "noargs")
    assert r.status == "pass" and "nothing to probe" in r.message


@pytest.mark.asyncio
async def test_unannotated_tool_skipped_without_flag():
    session = FakeSession(lambda args: (True, ""))
    t = AdvertisedTool(
        name="quiet",
        description=None,
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
        annotations={},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    r = _result_for(results, "quiet")
    assert r.status == "skip"
    assert r.details["skip_reason"] == "no_read_only_hint_asserted"

    forced = await REGISTRY.get("RUNTIME001").fn(
        _ctx(session, [t], allow_destructive=True)
    )
    rf = _result_for(forced, "quiet")
    assert rf.status == "fail" and rf.severity == "warning"
