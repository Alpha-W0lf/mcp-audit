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

Also carries:
- read_head        — reads the file at `path`, takes the first `head` BYTES,
  and decodes each aligned 1024-byte chunk independently with
  errors="replace": multi-byte UTF-8 straddling a chunk boundary becomes
  U+FFFD mojibake (ENCODING001, servers#4666 — must FAIL).
- read_head_safe   — same contract implemented correctly: decode the complete
  buffer once, then slice. ENCODING001 must PASS it.
- mojibake_read    — legacy canned stub returning replacement-mangled content
  regardless of arguments; ENCODING001 flags it too.
- drive_letter_create — accepts Windows drive-letter paths on POSIX and
  "creates" them as literal filenames; annotated readOnlyHint=false so the
  safety gate skips it unless --allow-destructive (PATHSAFE001, servers#4686
  — must FAIL under --allow-destructive).
- cite_doc         — returns a citation containing an absolute owner path
  ("/Users/tom/Documents/private/doc.md"): HYGIENE001 must FAIL it, and the
  finding must mask the user-directory segment rather than re-leak it.
- cite_doc_safe    — same contract implemented correctly: citations carry a
  stable server-relative source_id. HYGIENE001 must PASS it.

Built on the mcp SDK 2.x low-level API (constructor-registered handlers).
"""

from __future__ import annotations

from pathlib import Path

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
            # ENCODING001 (#4666): real chunked-decode corruption below.
            types.Tool(
                name="read_head",
                description="First N bytes of a file; decodes each 1024-byte "
                "chunk independently (corrupts straddling UTF-8).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "head": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            types.Tool(
                name="read_head_safe",
                description="First N characters of a file; decode-then-slice (boundary-safe).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "head": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            # Legacy canned stub; still probed by ENCODING001 and expected to
            # fail (returns U+FFFD garbage for any path).
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
            # PATHSAFE001 (#4686): drive-letter acceptance on POSIX; gated
            # behind readOnlyHint=false + --allow-destructive.
            types.Tool(
                name="drive_letter_create",
                description="Creates files; silently accepts C:\\ style paths on POSIX.",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=True),
            ),
            # HYGIENE001: citation leaking an absolute owner path vs the
            # correct source_id-based variant.
            types.Tool(
                name="cite_doc",
                description="Returns a citation for a document; leaks an absolute owner path.",
                inputSchema={
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
            ),
            types.Tool(
                name="cite_doc_safe",
                description="Returns a citation for a document; source_id-based (clean).",
                inputSchema={
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                },
                annotations=types.ToolAnnotations(read_only_hint=True),
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

    if params.name == "read_head":
        # BUG (#4666): fixed-size byte chunks decoded independently — a
        # multi-byte sequence straddling a 1024-byte boundary becomes U+FFFD.
        path = arguments.get("path", "")
        head = arguments.get("head")
        try:
            raw = Path(path).read_bytes()
        except OSError as e:
            return _error(f"cannot read {path!r}: {e}")
        data = raw[: int(head)] if head is not None else raw
        chunks = (data[i : i + 1024] for i in range(0, len(data), 1024))
        return _ok("".join(c.decode("utf-8", errors="replace") for c in chunks))

    if params.name == "read_head_safe":
        # Correct implementation: decode the complete buffer once, then slice.
        path = arguments.get("path", "")
        head = arguments.get("head")
        try:
            text = Path(path).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return _error(f"cannot read {path!r}: {e}")
        return _ok(text[: int(head)] if head is not None else text)

    if params.name == "mojibake_read":
        # BUG (#4666): replacement chars where a multi-byte sequence straddled
        # the 1024-byte boundary of the (padding-heavy) fixture text.
        return _ok("a" * 1100 + "\ufffd\ufffd broken sequence \ufffd")

    if params.name == "drive_letter_create":
        path = arguments.get("path", "")
        # BUG (#4686): POSIX host treats drive-letter path as literal filename.
        return _ok(f"created file named literally {path!r}")

    if params.name == "cite_doc":
        if "doc_id" not in arguments:
            return _error("Invalid arguments: missing required parameter 'doc_id'")
        # BUG (citation/path hygiene): ships the owner's absolute filesystem
        # path to every client instead of a server-relative identifier.
        doc_id = arguments["doc_id"]
        return _ok(f'citation for "{doc_id}": source /Users/tom/Documents/private/doc.md')

    if params.name == "cite_doc_safe":
        if "doc_id" not in arguments:
            return _error("Invalid arguments: missing required parameter 'doc_id'")
        # Correct shape: a stable, server-relative source identifier.
        return _ok(f'citation for "{arguments["doc_id"]}": source_id docs/report')

    return _error(f"unknown tool: {params.name}")


server = Server("buggy-fixture", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
