"""Tests for PATHSAFE001 — Windows drive-letter paths on POSIX hosts.

Layers:

1. Unit: the drive-letter classifier (`is_drive_letter_path`) and the
   path-property discovery heuristic (`path_like_properties`).
2. Branch unit tests against a fake session (conformant rejection -> PASS,
   unrelated error -> SKIP, missing --allow-destructive -> SKIP).
3. Integration-style against the bundled fixture server's buggy
   `drive_letter_create` tool over a real stdio subprocess.
4. Integration-style against a minimal real-filesystem server (materialized
   into tmp_path by this module) whose `write_note` exhibits #4686 exactly:
   the probe must FAIL, corroborate the literal backslash filename inside
   the sandbox root via details, and clean it up.

Integration tests spawn their own servers and do not depend on
MCP_FIXTURE_SERVER; they are tagged `integration` so `-m "not integration"`
deselects them like the rest of the dogfood suite.
"""

import sys
import textwrap
from pathlib import Path

import pytest
from mcp import types

from mcp_audit.checks.path_safety import (
    PROBE_VALUE,
    is_drive_letter_path,
    path_like_properties,
)
from mcp_audit.driver import AdvertisedTool, connect
from mcp_audit.registry import REGISTRY, CheckContext, load_checks

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_server.py"

CITATION = "https://github.com/modelcontextprotocol/servers/issues/4686"


class FakeSession:
    """Returns error iff a predicate over the arguments says so."""

    def __init__(self, behavior):
        self.behavior = behavior

    async def call_tool(self, name, arguments=None):
        ok, text = self.behavior(dict(arguments or {}))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=not ok
        )


def _tool(props=None, required=None, annotations=None, name="write_file") -> AdvertisedTool:
    props = props if props is not None else {"path": {"type": "string"}}
    return AdvertisedTool(
        name=name,
        description=None,
        input_schema={
            "type": "object",
            "properties": props,
            "required": required if required is not None else ["path"],
        },
        annotations=annotations if annotations is not None else {"readOnlyHint": True},
    )


def _ctx(session, tools, **kw) -> CheckContext:
    return CheckContext(session=session, tools=tools, **kw)


def _single(results):
    assert len(results) == 1
    return results[0]


@pytest.fixture(autouse=True)
def _load():
    load_checks()


# --- 1a. drive-letter classifier ---------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "C:\\Users\\me\\file.md",
        "Z:/x",
        "C:",
        "c:/lowercase/drive",
        "z:\\tmp\\out.txt",
    ],
)
def test_drive_letter_forms_accepted(value):
    assert is_drive_letter_path(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "notes/file.md",
        "dir\\file.txt",
        "/etc/passwd",
        "plain-name.txt",
        "\\\\server\\share\\x",
        "CC:/double-letter",
        "1:/digit-drive",
        ":no-drive-letter",
        "C",
        "C:x",
    ],
)
def test_non_drive_letter_forms_rejected(value):
    assert is_drive_letter_path(value) is False


def test_plain_backslash_names_out_of_scope_by_design():
    """`dir\\file.txt` stays out of scope: on POSIX a backslash is an ordinary
    filename character, so such a 'path' is a legitimate single filename
    INSIDE the sandbox — writing it is unusual naming, not the #4686
    sandbox-escape class (drive letters silently re-rooted as relative)."""
    assert is_drive_letter_path("dir\\file.txt") is False


# --- 1b. path-property heuristic ---------------------------------------------


def test_path_property_detected():
    assert path_like_properties(_tool()) == ["path"]


def test_profile_is_not_path_like():
    assert path_like_properties(_tool({"profile": {"type": "string"}}, ["profile"])) == []


def test_camel_case_and_variants_detected():
    t = _tool({"filePath": {"type": "string"}, "filename": {"type": "string"}})
    assert path_like_properties(t) == ["filePath", "filename"]


def test_non_string_properties_ignored():
    t = _tool({"path": {"type": "integer"}})
    assert path_like_properties(t) == []


def test_exact_path_wins_over_other_matches():
    t = _tool({"filepath": {"type": "string"}, "path": {"type": "string"}})
    assert path_like_properties(t)[0] == "path"


# --- 2. branch behavior with a fake session ----------------------------------


@pytest.mark.asyncio
async def test_conformant_rejection_passes():
    def behavior(args):
        if args.get("path") == PROBE_VALUE:
            return (
                False,
                f"Invalid path: absolute drive-letter {PROBE_VALUE} not allowed",
            )
        return True, "ok"

    results = await REGISTRY.get("PATHSAFE001").fn(
        _ctx(FakeSession(behavior), [_tool()], allow_destructive=True)
    )
    r = _single(results)
    assert r.status == "pass"
    assert r.severity == "error"
    assert r.citation == CITATION


@pytest.mark.asyncio
async def test_unrelated_error_skips():
    session = FakeSession(lambda args: (False, "connection reset by peer"))
    results = await REGISTRY.get("PATHSAFE001").fn(_ctx(session, [_tool()], allow_destructive=True))
    r = _single(results)
    assert r.status == "skip"
    assert r.details["skip_reason"] == "unattributable_error"


@pytest.mark.asyncio
async def test_error_echoing_probe_value_without_rejection_vocab_skips():
    """Overclaim trap: an unrelated error that merely echoes the probe value
    must NOT score PASS (no rejection vocabulary, no corroboration)."""
    session = FakeSession(
        lambda args: (False, f"cannot process {args.get('path')}: disk quota full")
    )
    results = await REGISTRY.get("PATHSAFE001").fn(_ctx(session, [_tool()], allow_destructive=True))
    r = _single(results)
    assert r.status == "skip"
    assert r.details["skip_reason"] == "unattributable_error"


@pytest.mark.asyncio
async def test_rejection_vocab_but_file_created_skips_and_cleans_up(tmp_path):
    """A 'rejection' that still materialized the probe file is not
    corroborated: score SKIP (never crash) and remove the artifact."""

    def behavior(args):
        if args.get("path") == PROBE_VALUE:
            (tmp_path / PROBE_VALUE).write_text("sneaky write", encoding="utf-8")
            return False, f"invalid path rejected: {PROBE_VALUE}"
        return True, "ok"

    literal = tmp_path / PROBE_VALUE
    results = await REGISTRY.get("PATHSAFE001").fn(
        _ctx(
            FakeSession(behavior),
            [_tool()],
            allow_destructive=True,
            extra={"sandbox_roots": [tmp_path]},
        )
    )
    r = _single(results)
    assert r.status == "skip"
    assert r.details["skip_reason"] == "rejection_not_corroborated"
    assert r.details["cleanup"] == "removed"
    assert not literal.exists()


@pytest.mark.asyncio
async def test_write_probe_requires_destructive_allowance():
    session = FakeSession(lambda args: (True, "should never be reached"))
    results = await REGISTRY.get("PATHSAFE001").fn(_ctx(session, [_tool()]))
    r = _single(results)
    assert r.status == "skip"
    assert r.details["skip_reason"] == "write_probe_requires_allow_destructive"


@pytest.mark.asyncio
async def test_allow_tool_does_not_satisfy_pathsafe_write_requirement():
    """--allow-tool permits read probes but PATHSAFE001 is a write probe and
    requires explicit --allow-destructive."""
    session = FakeSession(lambda args: (True, "should never be reached"))
    results = await REGISTRY.get("PATHSAFE001").fn(
        _ctx(session, [_tool()], allow_tools=("write_file",))
    )
    r = _single(results)
    assert r.status == "skip"
    assert r.details["skip_reason"] == "write_probe_requires_allow_destructive"


@pytest.mark.asyncio
async def test_no_path_tools_skips():
    session = FakeSession(lambda args: (True, ""))
    results = await REGISTRY.get("PATHSAFE001").fn(_ctx(session, [], allow_destructive=True))
    r = _single(results)
    assert r.status == "skip"
    assert "no tools" in r.message


# --- 3. integration: bundled fixture server ----------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fixture_drive_letter_tool_fails_under_allow_destructive():
    async with connect([sys.executable, str(FIXTURE)]) as handle:
        ctx = _ctx(handle.session, handle.tools, allow_destructive=True)
        results = await REGISTRY.get("PATHSAFE001").fn(ctx)

    by_tool = {r.tool_name: r for r in results}
    fail = by_tool["drive_letter_create"]
    assert fail.status == "fail"
    assert fail.severity == "error"
    assert fail.citation == CITATION
    assert fail.details["probe_value"] == PROBE_VALUE
    assert "#4686" in fail.message or CITATION in fail.citation
    assert by_tool["mojibake_read"].status == "skip"
    assert by_tool["mojibake_read"].details["skip_reason"] == "first_eligible_only"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fixture_drive_letter_tool_skipped_without_flag():
    async with connect([sys.executable, str(FIXTURE)]) as handle:
        results = await REGISTRY.get("PATHSAFE001").fn(_ctx(handle.session, handle.tools))

    by_tool = {r.tool_name: r for r in results}
    skipped = by_tool["drive_letter_create"]
    assert skipped.status == "skip"
    assert skipped.details["skip_reason"] == "destructive_hint_true"


# --- 4. integration: real-filesystem write_note reproduction -----------------

_BUGGY_SERVER_SOURCE = textwrap.dedent(
    """
    import os
    import sys

    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server


    def _ok(msg):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=msg)], is_error=False
        )


    async def on_list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="write_note",
                    description="Writes a note to the given path.",
                    inputSchema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    annotations=types.ToolAnnotations(read_only_hint=True),
                )
            ]
        )


    async def on_call_tool(ctx, params):
        path = (params.arguments or {}).get("path", "")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("mcp-audit probe")
        return _ok(f"wrote {path}")


    server = Server("pathsafe-buggy", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


    async def main():
        os.chdir(sys.argv[1])
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


    if __name__ == "__main__":
        import anyio

        anyio.run(main)
    """
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_filesystem_bug_fails_corroborates_and_cleans_up(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    server_script = tmp_path / "buggy_server.py"
    server_script.write_text(_BUGGY_SERVER_SOURCE, encoding="utf-8")

    literal = sandbox / "C:\\mcp-audit-probe.txt"

    async with connect([sys.executable, str(server_script), str(sandbox)]) as handle:
        ctx = _ctx(
            handle.session,
            handle.tools,
            allow_destructive=True,
            extra={"sandbox_roots": [str(sandbox)]},
        )
        results = await REGISTRY.get("PATHSAFE001").fn(ctx)

    r = _single(results)
    assert r.tool_name == "write_note"
    assert r.status == "fail"
    assert r.severity == "error"
    assert r.details["corroborated"] is True
    assert r.details["literal_file_created"] == str(literal)
    assert r.details["cleanup"] == "removed"
    assert not literal.exists(), "probe artifact must be deleted during cleanup"
