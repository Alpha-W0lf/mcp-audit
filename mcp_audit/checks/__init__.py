"""Conformance checks. Each module cites the real bug that seeded it.

Importing this package registers every built-in check with the global
registry (see mcp_audit.registry.load_checks).
"""

from __future__ import annotations

from mcp_audit.checks import encoding, hygiene, path_safety, runtime_required, schema

__all__ = ["encoding", "hygiene", "path_safety", "runtime_required", "schema"]
