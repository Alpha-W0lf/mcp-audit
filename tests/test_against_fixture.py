"""Dogfood integration test: audit the deliberately-buggy fixture server.

Skipped unless MCP_FIXTURE_SERVER names a runnable fixture command, e.g.:

    export MCP_FIXTURE_SERVER="python tests/fixtures/fixture_server.py"
    pytest -m integration

Coverage (v0.3):

- RUNTIME001 (dogfooded here):
  - strict_echo   -> passes (conformant rejection)
  - loose_required-> fails with severity=warning (schema stricter)
  - hidden_beta   -> fails with severity=error (runtime stricter);
                    overall CLI/report exit code is 1.
- ENCODING001 is covered by its own integration tests in test_encoding.py
  (fails `read_head` and `mojibake_read`, passes `read_head_safe`) (#4666).
- PATHSAFE001 is covered by its own integration tests in test_path_safety.py
  (fails `drive_letter_create` under --allow-destructive) (#4686).
"""

import json
import os
from pathlib import Path

import pytest

from mcp_audit.cli import main

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_server.py"


@pytest.fixture(scope="module")
def fixture_command() -> str:
    cmd = os.environ.get("MCP_FIXTURE_SERVER")
    if not cmd or not cmd.strip():
        pytest.skip("MCP_FIXTURE_SERVER not set; integration dogfood skipped")
    return cmd


def _run(command: str, *extra: str, json_path=None) -> dict | int:
    argv = ["run", "--server", command, *extra]
    if json_path:
        argv += ["--json", str(json_path)]
    code = main(argv)
    if json_path:
        return json.loads(Path(json_path).read_text())
    return code


def test_runtime001_fails_on_loose_schema_tool(fixture_command, tmp_path):
    """The spec's headline assertion: RUNTIME001 flags the loose-schema tool."""
    report = _run(fixture_command, json_path=tmp_path / "r.json")
    by_tool = {r["tool_name"]: r for r in report["results"] if r["check_id"] == "RUNTIME001"}

    assert by_tool["strict_echo"]["status"] == "pass"
    loose = by_tool["loose_required"]
    assert loose["status"] == "fail" and loose["severity"] == "warning"

    hidden = by_tool["hidden_beta"]
    assert hidden["status"] == "fail" and hidden["severity"] == "error"
    assert "beta" in hidden["message"]

    # error-severity failure trips the CI exit code; warnings do not
    assert report["exit_code"] == 1
