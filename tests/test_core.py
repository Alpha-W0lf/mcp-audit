"""Unit tests: models (exit codes, summary), registry, safety gate, CLI flags."""

import pytest

import mcp_audit.cli as cli
from mcp_audit.driver import AdvertisedTool
from mcp_audit.models import AuditReport, CheckResult
from mcp_audit.registry import REGISTRY, RegistryError, load_checks
from mcp_audit.safety import probe_eligibility


def _result(**kw) -> CheckResult:
    defaults: dict = {
        "check_id": "SCHEMA001",
        "severity": "error",
        "status": "pass",
        "message": "ok",
    }
    defaults.update(kw)
    return CheckResult(**defaults)


class TestExitCodes:
    def test_all_pass_is_zero(self):
        report = AuditReport(server_command=["x"], results=[_result()])
        assert report.exit_code == 0

    def test_skip_is_zero(self):
        report = AuditReport(server_command=["x"], results=[_result(status="skip")])
        assert report.exit_code == 0

    def test_failed_warning_is_zero(self):
        # warnings are the "harmless direction" by design
        report = AuditReport(
            server_command=["x"], results=[_result(status="fail", severity="warning")]
        )
        assert report.exit_code == 0

    def test_failed_error_is_one(self):
        report = AuditReport(server_command=["x"], results=[_result(status="fail")])
        assert report.exit_code == 1

    def test_failed_warning_is_one_under_strict(self):
        report = AuditReport(
            server_command=["x"],
            results=[_result(status="fail", severity="warning")],
            strict=True,
        )
        assert report.exit_code == 1

    def test_failed_error_still_one_under_strict(self):
        report = AuditReport(server_command=["x"], results=[_result(status="fail")], strict=True)
        assert report.exit_code == 1

    def test_summary_counts(self):
        report = AuditReport(
            server_command=["x"],
            results=[
                _result(status="pass"),
                _result(status="fail"),
                _result(status="fail", severity="warning"),
                _result(status="skip"),
            ],
        )
        s = report.summary
        assert s["status"] == {"pass": 1, "fail": 2, "skip": 1}
        assert s["failed_by_severity"] == {"error": 1, "warning": 1}
        assert s["total"] == 4

    def test_to_json_roundtrip_shape(self):
        report = AuditReport(server_command=["node", "srv.js"], results=[_result()])
        data = __import__("json").loads(report.to_json())
        assert data["server_command"] == ["node", "srv.js"]
        assert data["exit_code"] == 0
        assert data["strict"] is False
        assert data["results"][0]["check_id"] == "SCHEMA001"

    def test_to_json_serializes_strict(self):
        report = AuditReport(server_command=["x"], results=[], strict=True)
        assert __import__("json").loads(report.to_json())["strict"] is True

    def test_invalid_enums_rejected(self):
        with pytest.raises(ValueError):
            _result(severity="fatal")
        with pytest.raises(ValueError):
            _result(status="pending")


class TestRegistry:
    def test_load_checks_registers_exact_builtin_set(self):
        load_checks()
        assert set(REGISTRY.ids()) == {
            "SCHEMA001",
            "RUNTIME001",
            "ENCODING001",
            "PATHSAFE001",
            "HYGIENE001",
        }

    def test_duplicate_ids_rejected(self):
        from mcp_audit.registry import CheckRegistry

        reg = CheckRegistry()
        deco = reg.register(id="TEST001", severity="error", citation=None, scope="schema")

        async def fn(ctx):  # pragma: no cover
            return None

        deco(fn)
        with pytest.raises(RegistryError, match="duplicate"):
            deco(fn)

    def test_bad_id_format_rejected(self):
        from mcp_audit.registry import CheckRegistry

        reg = CheckRegistry()
        with pytest.raises(RegistryError, match="stable-ID"):
            reg.register(id="schema-one", severity="error", citation=None, scope="schema")

    def test_unknown_id_lookup_lists_known(self):
        load_checks()
        with pytest.raises(KeyError, match="SCHEMA001"):
            REGISTRY.get("NOPE999")


class TestSafetyGate:
    def _tool(self, annotations: dict | None) -> AdvertisedTool:
        return AdvertisedTool(
            name="t", description=None, input_schema={}, annotations=annotations or {}
        )

    def test_read_only_true_is_eligible(self):
        d = probe_eligibility(self._tool({"readOnlyHint": True}))
        assert d.eligible

    def test_read_only_false_skipped(self):
        d = probe_eligibility(self._tool({"readOnlyHint": False}))
        assert not d.eligible

    def test_destructive_skipped(self):
        d = probe_eligibility(self._tool({"readOnlyHint": False, "destructiveHint": True}))
        assert not d.eligible and d.outcome == "destructive_hint_true"

    def test_unannotated_defaults_to_ineligible(self):
        d = probe_eligibility(self._tool(None))
        assert not d.eligible and d.outcome == "no_read_only_hint_asserted"

    def test_destructive_with_read_only_true_still_eligible(self):
        # MCP spec: destructiveHint only has meaning when readOnlyHint==false
        d = probe_eligibility(self._tool({"readOnlyHint": True, "destructiveHint": True}))
        assert d.eligible

    def test_allow_destructive_overrides_everything(self):
        d = probe_eligibility(
            self._tool({"readOnlyHint": False, "destructiveHint": True}),
            allow_destructive=True,
        )
        assert d.eligible

    def test_allow_tool_overrides_gate_for_named_tool(self):
        d = probe_eligibility(
            self._tool({"readOnlyHint": False, "destructiveHint": True}),
            allow_tools=("t",),
        )
        assert d.eligible
        assert "--allow-tool t" in d.reason

    def test_allow_tool_leaves_gate_in_force_for_other_tools(self):
        d = probe_eligibility(
            self._tool({"readOnlyHint": False, "destructiveHint": True}),
            allow_tools=("someone_else",),
        )
        assert not d.eligible and d.outcome == "destructive_hint_true"


class TestCliFlags:
    """--strict plumbing and --allow-tool/--allow-destructive conflict.

    run_checks is monkeypatched so no server subprocess is spawned: the fake
    returns a report whose only finding is a failed warning, isolating the
    exit-code policy and the flag wiring.
    """

    def _run_with_warning_report(self, monkeypatch, argv_extra) -> int:
        async def fake_run_checks(command, **kw):
            return AuditReport(
                server_command=list(command),
                results=[_result(status="fail", severity="warning")],
                strict=kw.get("strict", False),
            )

        monkeypatch.setattr(cli, "run_checks", fake_run_checks)
        return cli.main(["run", "--server", "true", *argv_extra])

    def test_warning_fail_exits_zero_without_strict(self, monkeypatch):
        assert self._run_with_warning_report(monkeypatch, []) == 0

    def test_warning_fail_exits_one_with_strict(self, monkeypatch):
        assert self._run_with_warning_report(monkeypatch, ["--strict"]) == 1

    def test_allow_tool_conflicts_with_allow_destructive(self, capsys):
        code = cli.main(
            ["run", "--server", "true", "--allow-destructive", "--allow-tool", "write_file"]
        )
        assert code == 2
        assert "mutually exclusive" in capsys.readouterr().err
