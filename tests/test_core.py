"""Unit tests: models (exit codes, summary), registry, safety gate."""

import pytest

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
        assert data["results"][0]["check_id"] == "SCHEMA001"

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
