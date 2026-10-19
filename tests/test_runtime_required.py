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
    assert baseline_arguments(_tool(["a"])) == {"a": "mcp-audit-probe"}
    assert baseline_arguments(_tool(["n"], {"n": {"type": "integer"}})) == {"n": 1}
    # Constraint-aware: minimum/maxLength/minLength are honored (the official
    # sequentialthinking server requires thoughtNumber >= 1 — synthesis of 0
    # made the baseline fail and masked the #4651 mismatch).
    assert baseline_arguments(_tool(["n"], {"n": {"type": "integer", "minimum": 5}})) == {"n": 5}
    assert baseline_arguments(_tool(["b"], {"b": {"type": "boolean"}})) == {"b": True}
    long_s = baseline_arguments(_tool(["s"], {"s": {"type": "string", "minLength": 20}}))
    assert long_s is not None
    assert long_s["s"].startswith("mcp-audit-") and len(long_s["s"]) >= 20


def test_baseline_unsynthesizable_returns_none():
    t = _tool(["mystery"], {"mystery": {}})
    assert baseline_arguments(t) is None


# --- pattern-constrained string synthesis ------------------------------------


def test_pattern_literal_prefix_derives_value():
    # ^thought- -> "thought-mcp-audit" (field name and "mcp-audit" don't match)
    t = _tool(["thought"], {"thought": {"type": "string", "pattern": "^thought-"}})
    assert baseline_arguments(t) == {"thought": "thought-mcp-audit"}


def test_pattern_character_class_matches_field_name():
    t = _tool(["thought"], {"thought": {"type": "string", "pattern": "^[a-z]+$"}})
    assert baseline_arguments(t) == {"thought": "thought"}


def test_pattern_character_class_with_length_bounds():
    t = _tool(["token"], {"token": {"type": "string", "pattern": "^[a-z]{3,10}$"}})
    assert baseline_arguments(t) == {"token": "token"}


def test_pattern_unsatisfiable_marks_unsynthesizable():
    # no candidate matches ^\d+$ -> baseline skip, not an invalid probe value
    t = _tool(["thought"], {"thought": {"type": "string", "pattern": r"^\d+$"}})
    assert baseline_arguments(t) is None


def test_pattern_invalid_regex_marks_unsynthesizable():
    t = _tool(["x"], {"x": {"type": "string", "pattern": "(["}})
    assert baseline_arguments(t) is None


def test_pattern_respects_max_length():
    # every candidate exceeds maxLength -> unsynthesizable, not truncated junk
    t = _tool(
        ["thought"],
        {"thought": {"type": "string", "pattern": "^[a-z]+$", "maxLength": 3}},
    )
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
async def test_no_required_fields_is_probed_not_trivially_passed():
    calls: list[dict] = []

    def behavior(args):
        calls.append(args)
        return True, "ok"

    t = AdvertisedTool(
        name="noargs",
        description=None,
        input_schema={"type": "object", "properties": {}},
        annotations={"readOnlyHint": True},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(FakeSession(behavior), [t]))
    r = _result_for(results, "noargs")
    assert calls == [{}]
    assert r.status == "pass" and "accepted" in r.message


@pytest.mark.asyncio
async def test_zero_required_tool_with_runtime_required_field_fails():
    # tools/list advertises nothing as required, but the runtime demands `x`:
    # the #4651 shape in its most extreme form.
    session = FakeSession(
        lambda args: (
            (False, "Invalid arguments: missing required parameter 'x'")
            if "x" not in args
            else (True, "ok")
        )
    )
    t = AdvertisedTool(
        name="hidden_x",
        description=None,
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        annotations={"readOnlyHint": True},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    r = _result_for(results, "hidden_x")
    assert r.status == "fail" and r.severity == "error"
    assert "'x'" in r.message


@pytest.mark.asyncio
async def test_zero_required_tool_unrelated_error_naming_optional_field_skips():
    # "rate limit exceeded" mentions the optional `limit` property but is not
    # a validation error; it must not become an error-severity finding.
    session = FakeSession(lambda args: (False, "rate limit exceeded, retry later"))
    t = AdvertisedTool(
        name="search",
        description=None,
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        annotations={"readOnlyHint": True},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    assert _result_for(results, "search").status == "skip"


@pytest.mark.parametrize(
    "error_text", ["rate limit exceeded, retry later", "requests must be spaced 1s apart"]
)
@pytest.mark.asyncio
async def test_baseline_error_without_validation_wording_skips(error_text):
    # The error mentions an optional property but is not argument validation.
    session = FakeSession(lambda args: (False, error_text))
    t = AdvertisedTool(
        name="search",
        description=None,
        input_schema={
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer"},
                "requests": {"type": "integer"},
            },
            "required": ["q"],
        },
        annotations={"readOnlyHint": True},
    )
    results = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t]))
    assert _result_for(results, "search").status == "skip"


@pytest.mark.asyncio
async def test_all_inconclusive_omissions_skip():
    def behavior(args):
        if "a" in args and "b" in args:
            return True, "ok"
        return False, "internal server error"

    results = await REGISTRY.get("RUNTIME001").fn(_ctx(FakeSession(behavior), [_tool(["a", "b"])]))
    r = _result_for(results)
    assert r.status == "skip" and "inconclusive" in r.message


@pytest.mark.asyncio
async def test_pass_message_counts_inconclusive_omissions():
    def behavior(args):
        if "a" not in args:
            return False, "internal server error"  # names no field: inconclusive
        if "b" not in args:
            return False, "missing required parameter 'b'"
        return True, "ok"

    results = await REGISTRY.get("RUNTIME001").fn(_ctx(FakeSession(behavior), [_tool(["a", "b"])]))
    r = _result_for(results)
    assert r.status == "pass"
    assert "1 of 2" in r.message and "all 2" not in r.message


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

    forced = await REGISTRY.get("RUNTIME001").fn(_ctx(session, [t], allow_destructive=True))
    rf = _result_for(forced, "quiet")
    assert rf.status == "fail" and rf.severity == "warning"


@pytest.mark.asyncio
async def test_allow_tool_probes_named_tool_only():
    # --allow-tool grants per-tool consent: the named destructive tool is
    # probed while the annotation gate stays in force for the other one.
    session = FakeSession(lambda args: (True, ""))

    def write_tool(name):
        return AdvertisedTool(
            name=name,
            description=None,
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
            },
            annotations={"readOnlyHint": False, "destructiveHint": True},
        )

    results = await REGISTRY.get("RUNTIME001").fn(
        _ctx(
            session,
            [write_tool("allowed_write"), write_tool("other_write")],
            allow_tools=("allowed_write",),
        )
    )
    probed = _result_for(results, "allowed_write")
    assert probed.status == "fail" and probed.severity == "warning"
    skipped = _result_for(results, "other_write")
    assert skipped.status == "skip"
    assert skipped.details["skip_reason"] == "destructive_hint_true"
