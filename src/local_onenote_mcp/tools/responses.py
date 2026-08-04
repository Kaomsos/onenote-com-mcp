"""MCP response envelope mapping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..services import PartialFailure


def ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, "complete": True, "warnings": [], **data}


def error(message: str, code: str = "operation_failed", **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, "complete": False, **details}


def caught(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, PartialFailure):
        return error(str(exc), "partial_failure", **exc.details)
    if isinstance(exc, PermissionError):
        code = "policy_disabled"
    elif isinstance(exc, ValueError):
        code = "validation_error"
    else:
        code = "backend_error"
    return error(str(exc), code)


def invoke(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return ok(**action())
    except Exception as exc:
        return caught(exc)
