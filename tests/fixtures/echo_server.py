"""Trivial *correct* MCP echo server used for non-integration CLI smoke tests.

One tool, `echo`, with a well-formed schema, readOnlyHint=true, and runtime
validation that actually matches the advertised contract. Auditing this server
must produce zero failures (exit code 0).

Built on the mcp SDK 2.x low-level API (constructor-registered handlers).
"""

from __future__ import annotations

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def _error(msg: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=msg)], is_error=True)


def _ok(msg: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=msg)], is_error=False)


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="echo",
                description="Echo the provided text back.",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            )
        ]
    )


async def on_call_tool(ctx, params) -> types.CallToolResult:
    if params.name == "echo":
        arguments = params.arguments or {}
        if "text" not in arguments:
            return _error("Invalid arguments: missing required parameter 'text'")
        return _ok(f"echo: {arguments['text']}")
    return _error(f"unknown tool: {params.name}")


server = Server("echo-fixture", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
