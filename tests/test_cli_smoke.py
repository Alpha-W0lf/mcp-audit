"""CLI smoke test: run the full CLI against the trivial echo fixture server.

Marked integration — it spawns a real subprocess (server-spawning tests are
uniformly tagged so `-m "not integration"` selects the unit-only suite) but
only depends on this repo (tests/fixtures/echo_server.py) and proves
end-to-end wiring: driver -> registry -> checks -> report -> exit code.
"""

import json
import sys
from pathlib import Path

import pytest

from mcp_audit.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "echo_server.py"

pytestmark = pytest.mark.integration


def test_cli_run_against_echo_fixture_is_clean(tmp_path):
    json_out = tmp_path / "report.json"
    code = main(
        [
            "run",
            "--server",
            f"{sys.executable} {FIXTURE}",
            "--json",
            str(json_out),
        ]
    )
    assert code == 0, "a correct server must audit clean"

    report = json.loads(json_out.read_text())
    assert report["summary"]["status"]["fail"] == 0

    runtime = [
        r for r in report["results"] if r["check_id"] == "RUNTIME001" and r["tool_name"] == "echo"
    ]
    assert runtime and runtime[0]["status"] == "pass"


def test_cli_only_filter_and_skip(tmp_path):
    code = main(["run", "--server", f"{sys.executable} {FIXTURE}", "--only", "SCHEMA001"])
    assert code == 0

    # --skip with an unknown id is a usage error (exit 2)
    code = main(["run", "--server", "true", "--skip", "NOT_A_CHECK"])
    assert code == 2


def test_cli_startup_failure_exits_one(capsys):
    code = main(["run", "--server", "/bin/sh -c 'exit 7'", "--startup-timeout", "5"])
    captured = capsys.readouterr()
    assert code == 1
    assert "server failed to start" in captured.err


def test_list_checks_runs():
    assert main(["list-checks"]) == 0
