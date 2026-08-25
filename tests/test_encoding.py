"""Tests for ENCODING001 — chunk-boundary UTF-8 corruption (#4666).

Layers, mirroring test_runtime_required.py conventions:

- Pure fixture-builder unit tests: the crafted bytes must decode to U+FFFD
  when chunk-decoded at 1024/2048 independently, yet stay valid UTF-8 whole.
- Check-branch tests against a FakeSession replaying buggy/correct readers,
  the annotation gate, error degradation, and no-candidate discovery.
- Inline integration: spawn the bundled buggy fixture server subprocess and
  assert ENCODING001 fails `read_head` by name while `read_head_safe` passes;
  spawn echo_server and assert the no-file-reader skip.
"""

import sys
from pathlib import Path

import pytest
from mcp import types

from mcp_audit.checks.encoding import (
    BOUNDARIES,
    CHECK_ID,
    MARKER,
    boundary_fixture,
    find_limit_argument,
    find_path_argument,
    is_file_reader,
)
from mcp_audit.driver import AdvertisedTool, connect
from mcp_audit.registry import REGISTRY, CheckContext, load_checks

FIXTURES = Path(__file__).parent / "fixtures"
BUGGY_SERVER = FIXTURES / "fixture_server.py"
ECHO_SERVER = FIXTURES / "echo_server.py"


def _tool(properties=None, required=None, annotations=None) -> AdvertisedTool:
    props = properties or {"path": {"type": "string"}}
    return AdvertisedTool(
        name="read_thing",
        description=None,
        input_schema={
            "type": "object",
            "properties": props,
            "required": required if required is not None else ["path"],
        },
        annotations=annotations if annotations is not None else {"readOnlyHint": True},
    )


class FakeSession:
    """behavior(dict(arguments)) -> (ok: bool, text: str)."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls: list[dict] = []

    async def call_tool(self, name, arguments=None):
        args = dict(arguments or {})
        self.calls.append(args)
        ok, text = self.behavior(args)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=not ok
        )


class ExplodingSession:
    async def call_tool(self, name, arguments=None):
        raise RuntimeError("connection reset mid-read")


@pytest.fixture(autouse=True)
def _load():
    load_checks()


async def _run(session, tools, **kw) -> list:
    ctx = CheckContext(session=session, tools=tools, **kw)
    return await REGISTRY.get(CHECK_ID).fn(ctx)


# --- pure fixture builder -----------------------------------------------------


def test_fixture_is_valid_utf8_when_decoded_whole():
    data = boundary_fixture()
    text = data.decode("utf-8")  # must not raise
    assert text.count(MARKER) == len(BOUNDARIES)


def test_fixture_places_marker_exactly_at_boundaries():
    data = boundary_fixture()
    marker = MARKER.encode("utf-8")
    assert len(marker) == 3
    for boundary in BOUNDARIES:
        start = boundary - 2
        assert data[start : start + 3] == marker


def test_independent_chunk_decode_produces_replacement_chars():
    """The #4666 bug shape, reproduced on the fixture bytes alone."""
    data = boundary_fixture()
    for boundary in BOUNDARIES:
        head = data[:boundary]
        with pytest.raises(UnicodeDecodeError):
            head.decode("utf-8")
        assert "\ufffd" in head.decode("utf-8", errors="replace")
    # an interior aligned chunk starting mid-sequence also corrupts
    assert "\ufffd" in data[1024:2048].decode("utf-8", errors="replace")


def test_correct_decode_then_slice_keeps_marker_intact():
    data = boundary_fixture()
    text = data.decode("utf-8")
    assert MARKER in text[:1024]
    assert MARKER in text[:2048]


# --- discovery helpers --------------------------------------------------------


def test_path_argument_discovery():
    assert find_path_argument(_tool()) == "path"
    assert (
        find_path_argument(
            _tool({"file_path": {"type": "string"}, "head": {"type": "integer"}})
        )
        == "file_path"
    )
    assert find_path_argument(_tool({"text": {"type": "string"}})) is None
    # bounded-token matching: substrings like "profile" are NOT file args
    assert find_path_argument(_tool({"profile": {"type": "string"}})) is None


def test_limit_argument_discovery():
    tool = _tool({"path": {"type": "string"}, "head": {"type": "integer"}})
    assert find_limit_argument(tool) == "head"
    assert find_limit_argument(_tool({"path": {"type": "string"}})) is None
    # strings named like limits do not count; only integer params do
    not_limit = _tool({"path": {"type": "string"}, "count": {"type": "string"}})
    assert find_limit_argument(not_limit) is None


def test_echo_style_tool_is_not_a_file_reader():
    echo = AdvertisedTool(
        name="echo",
        description=None,
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        annotations={"readOnlyHint": True},
    )
    assert not is_file_reader(echo)


# --- check branches -----------------------------------------------------------


@pytest.mark.asyncio
async def test_buggy_chunked_reader_fails_naming_tool_and_boundary(tmp_path):
    """Replay of #4666: independent per-chunk decode -> U+FFFD mojibake."""

    def buggy_reader(args):
        raw = Path(args["path"]).read_bytes()
        limit = args.get("head")
        data = raw[: int(limit)] if limit is not None else raw
        chunks = (data[i : i + 1024] for i in range(0, len(data), 1024))
        return True, "".join(c.decode("utf-8", errors="replace") for c in chunks)

    session = FakeSession(buggy_reader)
    tool = _tool({"path": {"type": "string"}, "head": {"type": "integer"}})
    results = await _run(session, [tool])
    assert len(results) == 1
    r = results[0]
    assert r.status == "fail" and r.severity == "error"
    assert r.tool_name == "read_thing"
    assert "read_thing" in r.message
    assert "1024" in r.message
    assert "\ufffd" in r.message or "U+FFFD" in r.message
    # both boundaries probed because the tool advertises an integer `head`
    requested = sorted(p["requested_boundary"] for p in r.details["probes"])
    assert requested == [1024, 2048]


@pytest.mark.asyncio
async def test_correct_reader_passes_with_marker_intact():
    def correct_reader(args):
        text = Path(args["path"]).read_bytes().decode("utf-8")
        limit = args.get("head")
        return True, text[: int(limit)] if limit is not None else text

    session = FakeSession(correct_reader)
    results = await _run(session, [_tool()])
    r = results[0]
    assert r.status == "pass" and r.severity == "error"
    assert "intact" in r.message


@pytest.mark.asyncio
async def test_whole_file_read_probes_all_boundaries_without_limit_param():
    captured = {}

    def canned_buggy(args):
        captured["args"] = args
        return True, "\ufffd broken"

    session = FakeSession(canned_buggy)
    tool = _tool()  # no integer limit property -> single full-read probe
    results = await _run(session, [tool])
    r = results[0]
    assert r.status == "fail"
    assert len(r.details["probes"]) == 1
    assert set(captured["args"]) == {"path"}
    # both markers expected missing from the full read
    assert "1022" in r.message and "2046" in r.message


@pytest.mark.asyncio
async def test_no_file_reading_tool_skips_entirely():
    session = FakeSession(lambda args: (True, ""))
    echo_like = AdvertisedTool(
        name="echo",
        description=None,
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        annotations={"readOnlyHint": True},
    )
    results = await _run(session, [echo_like])
    assert len(results) == 1
    r = results[0]
    assert r.status == "skip"
    assert r.message == "no file-reading tool to probe"
    assert r.tool_name is None


@pytest.mark.asyncio
async def test_unannotated_file_tool_skipped_by_safety_gate():
    session = FakeSession(lambda args: (True, ""))
    results = await _run(session, [_tool(annotations={})])
    r = results[0]
    assert r.status == "skip"
    assert r.details["skip_reason"] == "no_read_only_hint_asserted"


@pytest.mark.asyncio
async def test_destructive_file_tool_skipped_by_safety_gate():
    session = FakeSession(lambda args: (True, ""))
    tool = _tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    results = await _run(session, [tool])
    assert results[0].status == "skip"
    assert results[0].details["skip_reason"] == "destructive_hint_true"


@pytest.mark.asyncio
async def test_allow_destructive_overrides_gate_and_probes():
    session = FakeSession(lambda args: (True, MARKER))
    tool = _tool(annotations={"readOnlyHint": False})
    results = await _run(session, [tool], allow_destructive=True)
    assert results[0].status == "pass"


@pytest.mark.asyncio
async def test_tool_call_error_degrades_to_skip():
    results = await _run(ExplodingSession(), [_tool()])
    r = results[0]
    assert r.status == "skip"
    assert "RuntimeError" in r.message and "connection reset" in r.message


@pytest.mark.asyncio
async def test_is_error_result_degrades_to_skip():
    session = FakeSession(lambda args: (False, "file not found"))
    results = await _run(session, [_tool()])
    r = results[0]
    assert r.status == "skip"
    assert "file not found" in r.message


@pytest.mark.asyncio
async def test_extra_required_fields_skip_probe():
    session = FakeSession(lambda args: (True, MARKER))
    tool = _tool(
        properties={"path": {"type": "string"}, "mode": {"type": "string"}},
        required=["path", "mode"],
    )
    results = await _run(session, [tool])
    r = results[0]
    assert r.status == "skip"
    assert session.calls == []  # nothing was called
    assert "mode" in r.message


# --- integration: real subprocess servers -------------------------------------


@pytest.mark.asyncio
async def test_buggy_fixture_server_read_head_fails_safe_passes():
    async with connect([sys.executable, str(BUGGY_SERVER)]) as handle:
        ctx = CheckContext(session=handle.session, tools=handle.tools)
        results = await REGISTRY.get(CHECK_ID).fn(ctx)

    by_tool = {r.tool_name: r for r in results}

    buggy = by_tool["read_head"]
    assert buggy.status == "fail" and buggy.severity == "error"
    assert "read_head" in buggy.message
    assert "1024" in buggy.message
    assert "U+FFFD" in buggy.message

    safe = by_tool["read_head_safe"]
    assert safe.status == "pass"

    legacy_stub = by_tool["mojibake_read"]
    assert legacy_stub.status == "fail"

    gated = by_tool["drive_letter_create"]
    assert gated.status == "skip"
    # destructiveHint takes precedence in the shared safety gate
    assert gated.details["skip_reason"] == "destructive_hint_true"

    # non-file tools are not reported at all by this check
    assert "strict_echo" not in by_tool
    assert "loose_required" not in by_tool
    assert "hidden_beta" not in by_tool


@pytest.mark.asyncio
async def test_echo_server_yields_single_no_candidate_skip():
    async with connect([sys.executable, str(ECHO_SERVER)]) as handle:
        ctx = CheckContext(session=handle.session, tools=handle.tools)
        results = await REGISTRY.get(CHECK_ID).fn(ctx)

    assert len(results) == 1
    r = results[0]
    assert r.status == "skip"
    assert r.message == "no file-reading tool to probe"
