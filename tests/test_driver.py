"""Driver hardening tests: startup timeout, early-death detection, stderr capture.

Marked integration: these tests spawn real subprocesses.
"""

import pytest

from mcp_audit.driver import ServerStartupError, connect

pytestmark = pytest.mark.integration

SLOW_SERVER = ["sleep", "30"]  # never speaks MCP -> initialize must time out
DEAD_SERVER = ["/bin/sh", "-c", "echo 'boom from stderr' >&2; exit 7"]


@pytest.mark.asyncio
async def test_startup_timeout_raises_with_stderr_tail():
    with pytest.raises(ServerStartupError, match="did not complete initialization"):
        async with connect(SLOW_SERVER, startup_timeout=1.0):
            pass  # pragma: no cover


@pytest.mark.asyncio
async def test_early_death_raises_and_reports_stderr():
    with pytest.raises(ServerStartupError, match="died before/during initialize") as ei:
        async with connect(DEAD_SERVER, startup_timeout=5.0):
            pass  # pragma: no cover
    assert "boom from stderr" in str(ei.value)


@pytest.mark.asyncio
async def test_empty_command_rejected():
    with pytest.raises(ValueError, match="empty"):
        async with connect([], startup_timeout=1.0):
            pass  # pragma: no cover
