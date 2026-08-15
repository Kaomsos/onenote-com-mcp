"""Configured service access for transport-level tool functions."""

from __future__ import annotations

from ..services.operation_runtime import OperationRuntime


_runtime: OperationRuntime | None = None


def configure(runtime: OperationRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> OperationRuntime:
    if _runtime is None:
        raise RuntimeError("MCP operation runtime has not been configured.")
    return _runtime
