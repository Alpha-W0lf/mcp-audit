"""Deliberately-buggy MCP fixture server for dogfood integration tests.

Three read-only tools, each exhibiting a distinct conformance failure class:

- strict_echo     — correct tool; RUNTIME001 must PASS it
  (omitting advertised-required `text` is rejected, error names `text`).
- loose_required  — schema claims `must_have` is required but runtime never
  checks it. RUNTIME001 omits `must_have`, the call succeeds -> schema
  stricter than runtime -> WARNING (harmless direction).
- hidden_beta     — tools/list advertises only `alpha` as required, but the
  runtime validator demands `beta` too (checked first, mirroring how zod
  reports the first missing key). Omitting `alpha` yields an error naming
  `beta` (not in required) -> runtime stricter than advertised -> ERROR,
  exit code 1.

Also carries stubs for future checks:
- mojibake_read   — returns UTF-8-replacement-mangled content at a chunk
  boundary (TODO: ENCODING001, servers#4666).
- drive_letter_create — accepts Windows drive-letter paths on POSIX and
  "creates" them as literal filenames; annotated readOnlyHint=false so the
  safety gate skips it unless --allow-destructive (TODO: PATHSAFE001,
  servers#4686).

Built on the mcp SDK 2.x low-level API (constructor-registered handlers).
"""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def _error(msg: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=msg)], is_error=True
    )


def _ok(msg: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=msg)], is_error=False
    )


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="strict_echo",
                description="Echoes text; validates exactly what it advertises.",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            types.Tool(
                name="loose_required",
                description="Advertises a required field but never enforces it.",
                inputSchema={
                    "type": "object",
                    "properties": {"must_have": {"type": "string"}},
                    "required": ["must_have"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            types.Tool(
                name="hidden_beta",
                description="Runtime requires 'beta' which is NOT in advertised required.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "alpha": {"type": "string"},
                        "beta": {"type": "string"},
                    },
                    "required": ["alpha"],  # BUG: beta enforced at runtime
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            # TODO(ENCODING001): assert this tool's output is valid UTF-8 (#4666).
            types.Tool(
                name="mojibake_read",
                description="Reads a file chunk boundary; corrupts straddling UTF-8.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            # TODO(PATHSAFE001): assert rejection of drive-letter paths on POSIX
            # (#4686); gated behind readOnlyHint=false + --allow-destructive.
            types.Tool(
                name="drive_letter_create",
                description="Creates files; silently accepts C:\\ style paths on POSIX.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                annotations=types.ToolAnnotations(
                    read_only_hint=False, destructive_hint=True
                ),
            ),
        ]
    )


async def on_call_tool(ctx, params) -> types.CallToolResult:
    arguments = params.arguments or {}

    if params.name == "strict_echo":
        if "text" not in arguments:
            return _error("Invalid arguments: missing required parameter 'text'")
        return _ok(f"echo: {arguments['text']}")

    if params.name == "loose_required":
        # BUG (#4651 warning direction): ignores its advertised required field.
        return _ok(f"processed {len(arguments)} args")

    if params.name == "hidden_beta":
        if "alpha" not in arguments:
            if "beta" not in arguments:
                # Omission probe hits this first: complains about the
                # un-advertised field, exactly like a zod validator.
                return _error("Invalid arguments: missing required parameter 'beta'")
            return _error("Invalid arguments: missing required parameter 'alpha'")
        return _ok("echoed")

    if params.name == "mojibake_read":
        # BUG (#4666): replacement chars where a multi-byte sequence straddled
        # the 1024-byte boundary.
        return _ok("line1\n\ufffd\ufffd broken sequence \ufffd")

    if params.name == "drive_letter_create":
        path = arguments.get("path", "")
        # BUG (#4686): POSIX host treats drive-letter path as literal filename.
        return _ok(f"created file named literally {path!r}")

    return _error(f"unknown tool: {params.name}")


server = Server("buggy-fixture", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
