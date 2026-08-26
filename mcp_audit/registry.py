"""Check registry.

Checks are plain async functions decorated with `@check(...)` that receive a
`CheckContext` and return one or more `CheckResult`s (or None for "nothing to
report"). IDs are stable and validated at import time — CI pipelines suppress
by ID (`--skip RUNTIME001`), so an accidental rename is a breaking change.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession

from mcp_audit.driver import AdvertisedTool
from mcp_audit.models import CheckResult, Severity

CheckFn = Callable[["CheckContext"], Awaitable[CheckResult | list[CheckResult] | None]]

_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*\d+$")

VALID_SCOPES = ("schema", "runtime", "encoding", "path_safety", "hygiene")


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class CheckSpec:
    """Registration record for a check function."""

    id: str
    severity: Severity
    citation: str | None
    scope: str
    fn: CheckFn


@dataclass
class CheckContext:
    """Everything a check may touch. Checks must not spawn servers themselves."""

    session: ClientSession
    tools: list[AdvertisedTool]
    allow_destructive: bool = False
    # Per-tool probe consent (--allow-tool): named tools override the
    # annotation gate; mutually exclusive with allow_destructive at the CLI.
    allow_tools: tuple[str, ...] = ()
    call_timeout: float = 10.0
    extra: dict[str, Any] = field(default_factory=dict)


def sandbox_roots_from_extra(extra: dict[str, Any]) -> list[Path]:
    """Parse ``CheckContext.extra["sandbox_roots"]`` into existing directories.

    Single parser shared by checks that place probe fixtures (ENCODING001) or
    corroborate filesystem side effects (PATHSAFE001). Only the plural key is
    honored — the CLI sets it from ``--arg`` values and nothing else populates
    the context. Entries that are not existing directories are dropped: the
    audit tool never creates directories.
    """
    raw = extra.get("sandbox_roots")
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple, set)) else [raw]
    roots: list[Path] = []
    for item in items:
        try:
            path = Path(item)
        except TypeError:
            continue
        if path.is_dir():
            roots.append(path)
    return roots


def _validate(spec_id: str, severity: str, scope: str) -> None:
    if not _ID_RE.match(spec_id):
        raise RegistryError(
            f"check id {spec_id!r} violates the stable-ID format "
            f"(UPPERCASE prefix + digits, e.g. SCHEMA001)"
        )
    if severity not in ("error", "warning"):
        raise RegistryError(f"check {spec_id}: invalid severity {severity!r}")
    if scope not in VALID_SCOPES:
        raise RegistryError(
            f"check {spec_id}: invalid scope {scope!r}; expected one of {VALID_SCOPES}"
        )


class CheckRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, CheckSpec] = {}

    def register(
        self,
        *,
        id: str,
        severity: Severity,
        citation: str | None,
        scope: str,
    ) -> Callable[[CheckFn], CheckFn]:
        _validate(id, severity, scope)

        def decorator(fn: CheckFn) -> CheckFn:
            if id in self._checks:
                raise RegistryError(f"duplicate check id: {id}")
            self._checks[id] = CheckSpec(
                id=id, severity=severity, citation=citation, scope=scope, fn=fn
            )
            return fn

        return decorator

    def get(self, check_id: str) -> CheckSpec:
        try:
            return self._checks[check_id]
        except KeyError:
            raise KeyError(f"unknown check id {check_id!r} (registered: {self.ids()})") from None

    def ids(self) -> list[str]:
        return sorted(self._checks)

    def all(self) -> list[CheckSpec]:
        return [self._checks[i] for i in self.ids()]

    def __len__(self) -> int:
        return len(self._checks)


REGISTRY = CheckRegistry()


def check(
    *,
    id: str,
    severity: Severity,
    citation: str | None = None,
    scope: str = "schema",
) -> Callable[[CheckFn], CheckFn]:
    """Register the decorated function under a stable ID in the global registry."""
    return REGISTRY.register(id=id, severity=severity, citation=citation, scope=scope)


def load_checks() -> None:
    """Import all built-in check modules so their @check decorators run."""
    import mcp_audit.checks  # noqa: F401  (package __init__ imports submodules)

    if len(REGISTRY) == 0:  # pragma: no cover — import machinery failure
        raise RegistryError(
            "no checks registered after importing mcp_audit.checks; check modules failed to load"
        )
