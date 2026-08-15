"""MCP response envelope mapping."""

from __future__ import annotations

from typing import Any

from ..onenote_errors import OneNoteError
from ..services import MutationFailure, MutationPreflightFailure, PartialFailure


def ok(**data: Any) -> dict[str, Any]:
    payload = dict(data)
    execution = payload.pop("execution", {})
    warnings = payload.pop("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    return {
        "ok": True,
        "result": payload,
        "warnings": warnings,
        "execution": execution,
    }


def error(message: str, code: str = "operation_failed", **details: Any) -> dict[str, Any]:
    payload = dict(details)
    execution = payload.pop("execution", {})
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": payload,
        },
        "execution": execution,
    }


def caught(
    exc: Exception, *, execution: dict[str, Any] | None = None
) -> dict[str, Any]:
    runtime_details = {"execution": execution} if execution is not None else {}
    if isinstance(exc, MutationPreflightFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        return error(str(exc), exc.code, **details, **runtime_details)
    if isinstance(exc, MutationFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        return error(str(exc), exc.code, **details, **runtime_details)
    if isinstance(exc, PartialFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        details.setdefault("partial", True)
        details.setdefault("reconciliation", "partially_applied")
        details.setdefault("retryability", "manual_recovery_required")
        return error(str(exc), "partial_failure", **details, **runtime_details)
    if isinstance(exc, OneNoteError):
        return error(str(exc), exc.code, **exc.public_details(), **runtime_details)
    if isinstance(exc, PermissionError):
        code = "policy_disabled"
    elif isinstance(exc, ValueError):
        code = "validation_error"
    else:
        code = "backend_error"
    return error(str(exc), code, **runtime_details)


def invoke(
    operation: str,
    *,
    timeout_seconds: float | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    from .context import get_runtime

    outcome = get_runtime().execute(
        operation, arguments, timeout_seconds=timeout_seconds
    )
    execution = outcome.public_execution()
    if outcome.success:
        return ok(**dict(outcome.data or {}), execution=execution)
    assert outcome.error is not None
    return caught(outcome.error, execution=execution)
