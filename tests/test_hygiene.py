"""Unit tests for HYGIENE001 — absolute owner path leakage in tool outputs.

Covers the pure scanner (pattern classes, masking, false-positive floor) and
the check's branch behavior against a FakeSession, mirroring
test_encoding.py conventions. Integration coverage (buggy `cite_doc` vs
correct `cite_doc_safe`) lives in test_against_fixture.py.
"""

from mcp import types

from mcp_audit.checks.hygiene import CHECK_ID, scan_for_leaks
from mcp_audit.driver import AdvertisedTool
from mcp_audit.registry import CheckContext, load_checks


class TestScanForLeaks:
    def test_posix_macos_home_detected_and_masked(self):
        leaks = scan_for_leaks("source /Users/tom/Documents/private/doc.md done")
        assert ("posix_home_path", "/Users/<redacted>/…") in leaks
        assert all("tom" not in masked for _, masked in leaks)

    def test_posix_linux_home_detected_and_masked(self):
        leaks = scan_for_leaks("see /home/deploy/app/config.yaml")
        assert ("posix_home_path", "/home/<redacted>/…") in leaks

    def test_windows_user_path_detected_and_masked(self):
        leaks = scan_for_leaks(r"file at C:\Users\alice\Documents\x.docx end")
        assert any(cls == "windows_user_path" for cls, _ in leaks)
        assert all("alice" not in masked for _, masked in leaks)

    def test_server_root_detected(self):
        for text in (
            "/var/www/html/index.html",
            "/srv/data/export.csv",
            "/root/.ssh/config",
            "/opt/tools/local/bin/run.sh",
        ):
            classes = {cls for cls, _ in scan_for_leaks(text)}
            assert "server_root_path" in classes, text

    def test_clean_text_has_no_leaks(self):
        assert scan_for_leaks("citation: source_id docs/report") == []
        # Relative and sandbox-relative paths are not owner paths.
        assert scan_for_leaks("wrote mcp-audit-probe under the allowed root") == []
        assert scan_for_leaks("/tmp/scratch/out.txt") == []
        # /etc, /usr: system paths, but not owner-private or server-web roots.
        assert scan_for_leaks("/etc/hosts /usr/lib") == []

    def test_duplicate_leaks_deduplicated(self):
        leaks = scan_for_leaks("/Users/tom/a.md and /Users/tom/b.md")
        assert leaks.count(("posix_home_path", "/Users/<redacted>/…")) == 1


def _tool(name: str, annotations: dict | None = None) -> AdvertisedTool:
    return AdvertisedTool(
        name=name,
        description=None,
        input_schema={
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
        annotations=annotations if annotations is not None else {"readOnlyHint": True},
    )


class FakeSession:
    """behavior(name, arguments) -> (ok: bool, text: str)."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls: list[dict] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append(dict(arguments or {}))
        ok, text = self.behavior(name, arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=not ok
        )


class TestCheckBranches:
    def _ctx(self, tools, session, **kw):
        load_checks()
        return CheckContext(session=session, tools=tools, **kw)

    async def test_fail_on_leaky_response(self):
        from mcp_audit.checks.hygiene import check_path_hygiene

        session = FakeSession(
            lambda name, args: (True, "source /Users/tom/Documents/private/doc.md")
        )
        results = await check_path_hygiene(self._ctx([_tool("cite_doc")], session))
        (r,) = results
        assert r.check_id == CHECK_ID and r.status == "fail" and r.severity == "error"
        assert r.tool_name == "cite_doc"
        assert "posix_home_path" in r.message
        assert "/Users/tom" not in r.message
        assert "/Users/tom" not in str(r.details)
        # One benign baseline call, no more.
        assert len(session.calls) == 1

    async def test_pass_on_clean_response(self):
        from mcp_audit.checks.hygiene import check_path_hygiene

        session = FakeSession(lambda name, args: (True, "citation: source_id docs/report"))
        results = await check_path_hygiene(self._ctx([_tool("cite_doc_safe")], session))
        (r,) = results
        assert r.status == "pass"

    async def test_skip_on_gate_ineligible(self):
        from mcp_audit.checks.hygiene import check_path_hygiene

        session = FakeSession(lambda name, args: (True, "/Users/tom/x"))
        tool = _tool("write_thing", annotations={"readOnlyHint": False})
        results = await check_path_hygiene(self._ctx([tool], session))
        (r,) = results
        assert r.status == "skip" and session.calls == []

    async def test_skip_on_failed_baseline(self):
        from mcp_audit.checks.hygiene import check_path_hygiene

        session = FakeSession(lambda name, args: (False, "boom"))
        results = await check_path_hygiene(self._ctx([_tool("broken")], session))
        (r,) = results
        assert r.status == "skip"
        assert "baseline call failed" in r.message

    async def test_skip_when_no_tools(self):
        from mcp_audit.checks.hygiene import check_path_hygiene

        session = FakeSession(lambda name, args: (True, ""))
        results = await check_path_hygiene(self._ctx([], session))
        (r,) = results
        assert r.status == "skip" and r.tool_name is None
