"""Spawn an MCP server over stdio and expose its advertised tools.

Thin wrapper over the official MCP Python SDK so checks stay focused on
assertions rather than transport plumbing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass(frozen=True)
class AdvertisedTool:
    """A tool as the server advertised it in tools/list."""

    name: str
    description: str | None
    input_schema: dict[str, Any]

    @property
    def required_fields(self) -> list[str]:
        return list(self.input_schema.get("required", []))

    @property
    def properties(self) -> dict[str, Any]:
        return dict(self.input_schema.get("properties", {}))


@asynccontextmanager
async def stdio_server(
    command: Sequence[str],
) -> AsyncIterator[list[AdvertisedTool]]:
    """Launch an MCP server process, yield its advertised tools, clean up."""
    params = StdioServerParameters(command=command[0], args=list(command[1:]))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            yield [
                AdvertisedTool(
                    name=t.name,
                    description=t.description,
                    input_schema=t.inputSchema or {},
                )
                for t in result.tools
            ]
