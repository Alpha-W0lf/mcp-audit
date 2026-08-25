"""Spawn an MCP server over stdio and expose its advertised tools.

Hardened for v0.2:
- bounded startup: if the server fails to initialize within `startup_timeout`
  seconds, the process is torn down and `ServerStartupError` is raised with the
  captured stderr tail (a server that dies before initialize produces a clear
  error, not a hang);
- stderr capture: the server's stderr goes to an in-memory buffer, never into
  tool results or our own stdout — a chatty server cannot corrupt JSON output
  or masquerade as tool content;
- clean teardown: connection unwinds via context managers even when checks
  raise; the CLI additionally converts check exceptions into results;
- SDK exception-group hygiene: anyio task groups wrap errors raised while
  unwinding; `ServerStartupError` is surfaced unwrapped so callers can catch
  it directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

DEFAULT_STARTUP_TIMEOUT = 10.0
_STDERR_TAIL_BYTES = 8192


class ServerStartupError(RuntimeError):
    """Server failed to start or initialize within the timeout."""


async def call_tool_normalized(
    session: Any, name: str, arguments: dict[str, Any], timeout: float
) -> tuple[bool, str]:
    """Call a tool; normalize protocol errors and isError results into text.

    Shared probe primitive for all runtime checks. Returns
    (succeeded, message_or_content_text).
    """
    try:
        result: types.CallToolResult = await asyncio.wait_for(
            session.call_tool(name, arguments=arguments), timeout=timeout
        )
    except TimeoutError:
        return False, f"probe timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001 — protocol/connection failures are probe outcomes
        return False, f"{type(e).__name__}: {e}"

    text_parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, types.TextContent):
            text_parts.append(block.text)
        else:
            text_parts.append(f"<{type(block).__name__}>")
    return (not result.is_error), "\n".join(text_parts).strip()


@dataclass(frozen=True)
class AdvertisedTool:
    """A tool as the server advertised it in tools/list."""

    name: str
    description: str | None
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def required_fields(self) -> list[str]:
        return list(self.input_schema.get("required", []))

    @property
    def properties(self) -> dict[str, Any]:
        return dict(self.input_schema.get("properties", {}))


def _tool_annotations(t: types.Tool) -> dict[str, Any]:
    """SDK attribute names are snake_case; mcp-audit canonicalizes to the
    MCP wire-format (camelCase) keys so checks and tests use spec vocabulary."""
    ann = t.annotations
    if ann is None:
        return {}
    mapping = {
        "title": "title",
        "read_only_hint": "readOnlyHint",
        "destructive_hint": "destructiveHint",
        "idempotent_hint": "idempotentHint",
        "open_world_hint": "openWorldHint",
    }
    out: dict[str, Any] = {}
    for attr, wire in mapping.items():
        v = getattr(ann, attr, None)
        if v is not None:
            out[wire] = v
    return out


@dataclass
class ServerHandle:
    """Live session plus diagnostics; yielded by `connect`."""

    session: ClientSession
    _stderr_file: Any
    tools: list[AdvertisedTool] = field(default_factory=list)

    def stderr_tail(self) -> str:
        return _safe_tail(self._stderr_file)


@asynccontextmanager
async def _session_lifecycle(
    params: StdioServerParameters,
    errlog: Any,
    startup_timeout: float,
) -> AsyncIterator[ServerHandle]:
    # nested-with is deliberate: each layer owns its teardown ordering
    async with stdio_client(params, errlog=errlog) as (read, write):  # noqa: SIM117
        async with ClientSession(read, write) as session:
            try:
                await asyncio.wait_for(session.initialize(), timeout=startup_timeout)
                result = await asyncio.wait_for(session.list_tools(), timeout=startup_timeout)
            except TimeoutError as e:
                raise ServerStartupError(
                    f"server did not complete initialization within "
                    f"{startup_timeout}s. stderr tail:\n{_safe_tail(errlog)}"
                ) from e
            except Exception as e:
                raise ServerStartupError(
                    f"server died before/during initialize. stderr tail:\n{_safe_tail(errlog)}"
                ) from e
            yield ServerHandle(
                session=session,
                _stderr_file=errlog,
                tools=[
                    AdvertisedTool(
                        name=t.name,
                        description=t.description,
                        input_schema=t.input_schema or {},
                        annotations=_tool_annotations(t),
                    )
                    for t in result.tools
                ],
            )


def _find_startup_error(group: BaseExceptionGroup) -> ServerStartupError | None:
    """Depth-first search for ServerStartupError in a (possibly nested) group."""
    for exc in group.exceptions:
        if isinstance(exc, ServerStartupError):
            return exc
        if isinstance(exc, BaseExceptionGroup):
            found = _find_startup_error(exc)
            if found is not None:
                return found
    return None


@asynccontextmanager
async def connect(
    command: Sequence[str],
    *,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
) -> AsyncIterator[ServerHandle]:
    """Launch an MCP server process, yield its tools + session, clean up.

    Raises ServerStartupError if the process exits early or initialize/list_tools
    exceed `startup_timeout`.
    """
    if not command:
        raise ValueError("server command must not be empty")
    params = StdioServerParameters(command=command[0], args=list(command[1:]))
    # Text-mode temp file owned by us for the session's whole lifetime (the SDK
    # redirects the child's stderr here); closed in connect()'s finally.
    errlog = tempfile.TemporaryFile(  # noqa: SIM115
        mode="w+", encoding="utf-8", errors="replace"
    )
    try:
        try:
            async with _session_lifecycle(params, errlog, startup_timeout) as handle:
                yield handle
        except BaseExceptionGroup as eg:
            startup = _find_startup_error(eg)
            if startup is not None:
                raise startup from None
            raise
    finally:
        with contextlib.suppress(OSError):
            errlog.close()


def _safe_tail(errlog: Any) -> str:
    try:
        errlog.flush()
        pos = errlog.tell()
        errlog.seek(max(0, pos - _STDERR_TAIL_BYTES))
        data = errlog.read(_STDERR_TAIL_BYTES)
        errlog.seek(pos)
        return data.strip() or "<no stderr output captured>"
    except (OSError, ValueError):
        return "<stderr capture unavailable>"
