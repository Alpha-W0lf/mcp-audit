"""Probe-safety gating driven by MCP tool annotations.

The MCP spec defines tool `annotations` as *hints the server asserts about
itself*:

- ``readOnlyHint``   — the tool does not modify its environment (default: false)
- ``destructiveHint``— the tool may destructive-update its target; only
                       meaningful when readOnlyHint is false (default: true)

Because these are self-reported, unverified hints, this module treats them as a
*floor for caution, not a guarantee of safety*: an eligible tool may still run
arbitrary code inside the audited server process. Run mcp-audit against servers
you control or trust; `--allow-destructive` exists for explicitly sandboxed
environments.

Default posture: if a server omits annotations entirely, the tool is NOT
probe-eligible. Absence of evidence is treated as "may have side effects".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ELIGIBLE = "eligible"
SKIP_DESTRUCTIVE = "destructive_hint_true"
SKIP_NOT_READ_ONLY = "read_only_hint_false"
SKIP_UNANNOTATED = "no_read_only_hint_asserted"


@dataclass(frozen=True)
class ProbeDecision:
    outcome: str  # one of the constants above
    reason: str

    @property
    def eligible(self) -> bool:
        return self.outcome == ELIGIBLE


def _annotations(tool: Any) -> dict[str, Any]:
    """Plain-dict annotations, already canonicalized to wire-format keys.

    The snake_case->camelCase SDK mapping lives in exactly one place:
    mcp_audit.driver._tool_annotations, applied when tools are advertised.
    """
    ann = getattr(tool, "annotations", None)
    if isinstance(ann, dict):
        return dict(ann)
    return {}


def probe_eligibility(tool: Any, *, allow_destructive: bool = False) -> ProbeDecision:
    """Decide whether RUNTIME-style probes may call this tool.

    Precedence:
      1. --allow-destructive forces eligibility (operator override).
      2. destructiveHint=true -> never probe.
      3. readOnlyHint=false  -> never probe.
      4. readOnlyHint absent -> not eligible (safe default).
      5. readOnlyHint=true   -> eligible (destructiveHint is defined by the MCP
         spec to be meaningless when readOnlyHint is true).
    """
    if allow_destructive:
        return ProbeDecision(
            ELIGIBLE,
            "--allow-destructive passed; annotation gate overridden by operator",
        )
    ann = _annotations(tool)
    if ann.get("destructiveHint") is True and ann.get("readOnlyHint") is not True:
        return ProbeDecision(SKIP_DESTRUCTIVE, "server asserts destructiveHint=true")
    if ann.get("readOnlyHint") is False:
        return ProbeDecision(SKIP_NOT_READ_ONLY, "server asserts readOnlyHint=false")
    if ann.get("readOnlyHint") is not True:
        return ProbeDecision(
            SKIP_UNANNOTATED,
            "no readOnlyHint asserted — annotations are server-asserted hints, "
            "so absence is treated as potentially side-effecting",
        )
    return ProbeDecision(ELIGIBLE, "server asserts readOnlyHint=true")


def eligible_tools(tools: list[Any], *, allow_destructive: bool = False) -> list[Any]:
    return [t for t in tools if probe_eligibility(t, allow_destructive=allow_destructive).eligible]
