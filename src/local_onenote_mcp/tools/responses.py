"""MCP response envelope mapping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..onenote_errors import OneNoteError
from ..services import MutationFailure, MutationPreflightFailure, PartialFailure


def ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, "complete": True, "warnings": [], **data}


def error(message: str, code: str = "operation_failed", **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, "complete": False, **details}


def caught(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MutationPreflightFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        return error(str(exc), exc.code, **details)
    if isinstance(exc, MutationFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        return error(str(exc), exc.code, **details)
    if isinstance(exc, PartialFailure):
        details = dict(exc.details)
        details.setdefault("error_type", type(exc).__name__)
        details.setdefault("partial", True)
        details.setdefault("reconciliation", "partially_applied")
        details.setdefault("retryability", "manual_recovery_required")
        return error(str(exc), "partial_failure", **details)
    if isinstance(exc, OneNoteError):
        return error(str(exc), exc.code, **exc.public_details())
    if isinstance(exc, PermissionError):
        code = "policy_disabled"
    elif isinstance(exc, ValueError):
        code = "validation_error"
    else:
        code = "backend_error"
    return error(str(exc), code)


def invoke(
    action: Callable[[], dict[str, Any]],
    *,
    mutation: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    try:
        # Imported lazily to keep context -> services -> tools free of cycles.
        from .context import get_services

        coordinator = get_services().coordinator
        scope = coordinator.mutation if mutation else coordinator.read
        with scope(timeout_seconds=timeout_seconds):
            return ok(**action())
    except Exception as exc:
        return caught(exc)
