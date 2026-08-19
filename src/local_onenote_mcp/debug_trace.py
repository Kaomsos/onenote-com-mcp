"""Local MCP runtime debug trace: config, event schema, projection, and JSONL writer."""

from __future__ import annotations

import atexit
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from .policy import env_bool
from .services.errors import classify_error

if TYPE_CHECKING:
    from .services.operation_runtime import (
        BackendCategory,
        OperationExecution,
        OperationOutcome,
        OperationSpec,
    )

MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_SESSION_EVENTS = 4096
_DIAGNOSTIC_EMITTED = False
_UNSPECIFIED_PROJECTION = "unspecified"

_ALLOWED_OBSERVED_OUTCOMES = frozenset(
    {
        "completed",
        "failed",
        "applied",
        "partially_applied",
        "indeterminate",
        "not_observed",
        "accepted_completion_unobservable",
        "action_accepted",
        "filesystem_effect_completed",
    }
)

_ALLOWED_RETRY_SAFETY = frozenset(
    {
        "not_needed",
        "new_call_required",
        "unknown",
        "safe_to_retry",
        "after_user_action",
        "read_after_delay",
        "reconcile_before_retry",
        "manual_recovery_required",
    }
)


class TraceEvent(StrEnum):
    TOOL_CALL_ENTERED = "tool_call.entered"
    TOOL_CALL_VALIDATED = "tool_call.validated"
    TOOL_CALL_AUTHORIZED = "tool_call.authorized"
    TOOL_CALL_AUTHORIZATION_REJECTED = "tool_call.authorization_rejected"
    TOOL_CALL_PLATFORM_PREFLIGHT_STARTED = "tool_call.platform_preflight_started"
    TOOL_CALL_PLATFORM_PREFLIGHT_COMPLETED = "tool_call.platform_preflight_completed"
    TOOL_CALL_PLATFORM_PREFLIGHT_FAILED = "tool_call.platform_preflight_failed"
    TOOL_CALL_HANDLER_STARTED = "tool_call.handler_started"
    TOOL_CALL_FINALIZING = "tool_call.finalizing"
    TOOL_CALL_COMPLETED = "tool_call.completed"
    TOOL_CALL_FAILED = "tool_call.failed"
    TOOL_CALL_CANCELLED = "tool_call.cancelled"


_TERMINAL_EVENTS = frozenset(
    {
        TraceEvent.TOOL_CALL_COMPLETED,
        TraceEvent.TOOL_CALL_FAILED,
        TraceEvent.TOOL_CALL_CANCELLED,
    }
)


def default_debug_trace_dir() -> Path:
    """Return the user-local default trace root in one changeable location."""
    return Path.home() / ".onenote-mcp" / "debug-trace"


@dataclass(frozen=True)
class DebugTraceConfig:
    enabled: bool
    output_dir: str | None

    @classmethod
    def from_env(cls) -> "DebugTraceConfig":
        enabled = env_bool("LOCAL_ONENOTE_MCP_DEBUG_TRACE")
        configured_output_dir = os.environ.get("LOCAL_ONENOTE_MCP_DEBUG_DIR")
        output_dir = (
            configured_output_dir.strip()
            if configured_output_dir is not None
            else None
        )
        if enabled and configured_output_dir is None:
            output_dir = str(default_debug_trace_dir())
        elif enabled and not output_dir:
            raise ValueError(
                "LOCAL_ONENOTE_MCP_DEBUG_DIR must not be empty when set."
            )
        return cls(enabled=enabled, output_dir=output_dir)


def _emit_diagnostic(message: str) -> None:
    global _DIAGNOSTIC_EMITTED
    if _DIAGNOSTIC_EMITTED:
        return
    _DIAGNOSTIC_EMITTED = True
    print(message, file=sys.stderr, flush=True)


def _project_observed_outcome(value: str) -> str:
    return value if value in _ALLOWED_OBSERVED_OUTCOMES else _UNSPECIFIED_PROJECTION


def _project_retry_safety(value: str) -> str:
    return value if value in _ALLOWED_RETRY_SAFETY else _UNSPECIFIED_PROJECTION


def _validate_output_dir(path_text: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        raise ValueError("LOCAL_ONENOTE_MCP_DEBUG_DIR must be an absolute path.")
    if not candidate.exists():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(
                "LOCAL_ONENOTE_MCP_DEBUG_DIR could not be created."
            ) from exc
    if not candidate.is_dir():
        raise ValueError("LOCAL_ONENOTE_MCP_DEBUG_DIR must reference a directory.")
    attributes = getattr(os.lstat(candidate), "st_file_attributes", 0)
    if attributes & 0x400:
        raise ValueError("LOCAL_ONENOTE_MCP_DEBUG_DIR must not be a reparse point.")
    return candidate


def _project_argument_shape(arguments: Mapping[str, Any]) -> dict[str, Any]:
    keys: list[str] = []
    types: dict[str, str] = {}
    lengths: dict[str, int] = {}
    is_none: dict[str, bool] = {}
    for key in sorted(arguments):
        if not isinstance(key, str):
            continue
        value = arguments[key]
        keys.append(key)
        types[key] = type(value).__name__
        is_none[key] = value is None
        if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset)):
            lengths[key] = len(value)
    return {
        "keys": keys,
        "types": types,
        "lengths": lengths,
        "is_none": is_none,
    }


def _execution_summary(execution: "OperationExecution") -> dict[str, Any]:
    return {
        "backend_call_count": execution.backend_calls,
        "attempts": execution.attempts,
        "replayed": execution.replayed,
    }


def _project_error(error: Any) -> dict[str, Any]:
    return {
        "code": error.code,
        "error_type": error.error_type,
        "partial": error.partial,
        "indeterminate": error.indeterminate,
        "retry_safe": error.retry_safe,
    }


class _SessionWriter:
    def __init__(self, directory: Path) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._bytes_written = 0
        self._event_count = 0
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        suffix = secrets.token_hex(4)
        filename = f"session-{timestamp}-{os.getpid()}-{suffix}.jsonl"
        session_path = directory / filename
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(session_path), flags)
        self._handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        self._path = session_path

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: Mapping[str, Any]) -> None:
        try:
            line = json.dumps(dict(record), ensure_ascii=False) + "\n"
            encoded = line.encode("utf-8")
        except Exception:
            self._stop("debug_trace_write_failed")
            return
        with self._lock:
            if self._stopped:
                return
            if (
                self._event_count >= MAX_SESSION_EVENTS
                or self._bytes_written + len(encoded) > MAX_SESSION_BYTES
            ):
                self._close_locked("debug_trace_capacity_exceeded")
                return
            try:
                self._handle.write(line)
                self._handle.flush()
                self._bytes_written += len(encoded)
                self._event_count += 1
            except Exception:
                self._close_locked("debug_trace_write_failed")

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self, reason: str | None = None) -> None:
        if self._stopped and self._handle.closed:
            return
        if not self._handle.closed:
            try:
                self._handle.flush()
            except Exception:
                pass
            try:
                self._handle.close()
            except Exception:
                pass
        self._stopped = True
        if reason is not None:
            _emit_diagnostic(f"local-onenote-mcp debug trace stopped: {reason}")

    def _stop(self, reason: str) -> None:
        with self._lock:
            self._close_locked(reason)


class DebugTraceSpan:
    def __init__(
        self,
        *,
        tracer: "DebugTracer",
        tool_call_id: int,
        correlation_id: str,
        spec: "OperationSpec",
        argument_shape: Mapping[str, Any],
        started_monotonic: float,
    ) -> None:
        self._tracer = tracer
        self._tool_call_id = tool_call_id
        self._correlation_id = correlation_id
        self._spec = spec
        self._argument_shape = argument_shape
        self._started_monotonic = started_monotonic
        self._finished = False
        self._last_execution: "OperationExecution | None" = None

    def validated(self, execution: "OperationExecution") -> None:
        self._emit(
            TraceEvent.TOOL_CALL_VALIDATED,
            execution,
            argument_shape=dict(self._argument_shape),
        )

    def authorized(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_AUTHORIZED, execution)

    def authorization_rejected(
        self, execution: "OperationExecution", exc: Exception
    ) -> None:
        self._emit(
            TraceEvent.TOOL_CALL_AUTHORIZATION_REJECTED,
            execution,
            error=classify_error(exc),
        )

    def platform_preflight_started(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_STARTED, execution)

    def platform_preflight_completed(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_COMPLETED, execution)

    def platform_preflight_failed(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_FAILED, execution)

    def handler_started(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_HANDLER_STARTED, execution)

    def finalizing(self, execution: "OperationExecution") -> None:
        self._emit(TraceEvent.TOOL_CALL_FINALIZING, execution)

    def backend_dispatched(
        self,
        execution: "OperationExecution",
        category: "BackendCategory",
        *,
        operation: str,
    ) -> None:
        self._emit_backend(execution, category=category, operation=operation)

    def finish(self, outcome: "OperationOutcome") -> None:
        if self._finished:
            return
        event = (
            TraceEvent.TOOL_CALL_COMPLETED
            if outcome.success
            else TraceEvent.TOOL_CALL_FAILED
        )
        error = (
            None
            if outcome.success
            else classify_error(outcome.error or RuntimeError("missing error"))
        )
        execution = self._execution_from_outcome(outcome)
        self._emit(
            event,
            execution,
            error=error,
            outcome_stage=outcome.stage.value,
            observed_outcome=_project_observed_outcome(outcome.observed_outcome),
            retry_safety=_project_retry_safety(outcome.retry_safety),
            summary=_execution_summary(execution),
        )
        self._finished = True

    def cancel(self, execution: "OperationExecution") -> None:
        if self._finished:
            return
        source = self._last_execution or execution
        self._emit(
            TraceEvent.TOOL_CALL_CANCELLED,
            source,
            summary=_execution_summary(source),
        )
        self._finished = True

    def _execution_from_outcome(self, outcome: "OperationOutcome") -> "OperationExecution":
        from .services.operation_runtime import OperationExecution

        return OperationExecution(
            operation=outcome.operation,
            kind=outcome.kind,
            backend=outcome.backend,
            stage=outcome.stage,
            started_monotonic=self._started_monotonic,
            deadline_monotonic=self._started_monotonic,
            attempts=outcome.attempts,
            replayed=outcome.replayed,
            backend_calls=outcome.backend_calls,
            completed_steps=[dict(step) for step in outcome.completed_steps],
            generation_before=outcome.generation_before,
            generation_after=outcome.generation_after,
            observed_outcome=outcome.observed_outcome,
            retry_safety=outcome.retry_safety,
            recommended_action=outcome.recommended_action,
        )

    def _emit(
        self,
        event: TraceEvent,
        execution: "OperationExecution | None" = None,
        *,
        error: Any | None = None,
        **extra: Any,
    ) -> None:
        if self._finished and event not in _TERMINAL_EVENTS:
            return
        if execution is not None:
            self._last_execution = execution
        try:
            record: dict[str, Any] = {
                "tool_call_id": self._tool_call_id,
                "tool": self._spec.name,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": self._elapsed_seconds(),
                "event": event.value,
                "correlation_id": self._correlation_id,
            }
            if event is TraceEvent.TOOL_CALL_ENTERED:
                record["operation_kind"] = self._spec.kind.value
                record["operation_strategy"] = self._spec.strategy
            if error is not None:
                record["error"] = _project_error(error)
            for key, value in extra.items():
                if value is not None:
                    record[key] = value
            self._tracer._writer.append(record)
        except Exception:
            self._tracer._stop_writer("debug_trace_emit_failed")

    def _emit_backend(
        self,
        execution: "OperationExecution",
        *,
        category: "BackendCategory",
        operation: str,
    ) -> None:
        if self._finished:
            return
        self._last_execution = execution
        try:
            self._tracer._writer.append(
                {
                    "backend_call_id": execution.backend_calls,
                    "operation": operation,
                    "tool_call_id": self._tool_call_id,
                    "tool": self._spec.name,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": self._elapsed_seconds(),
                    "backend_category": category.value,
                    "correlation_id": self._correlation_id,
                }
            )
        except Exception:
            self._tracer._stop_writer("debug_trace_emit_failed")

    def _elapsed_seconds(self) -> float:
        return round(max(0.0, time.monotonic() - self._started_monotonic), 6)

    def __enter__(self) -> "DebugTraceSpan":
        try:
            self._emit(TraceEvent.TOOL_CALL_ENTERED, self._execution_stub())
        except Exception:
            self._tracer._stop_writer("debug_trace_emit_failed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: Any,
    ) -> bool:
        if exc_type is not None and not issubclass(exc_type, Exception):
            if not self._finished:
                try:
                    self.cancel(self._execution_stub())
                except Exception:
                    self._tracer._stop_writer("debug_trace_emit_failed")
            return False
        return False

    def _execution_stub(self) -> "OperationExecution":
        from .services.operation_runtime import OperationExecution, OperationStage

        return OperationExecution(
            operation=self._spec.name,
            kind=self._spec.kind,
            backend=self._spec.backend,
            stage=OperationStage.ADMISSION,
            started_monotonic=self._started_monotonic,
            deadline_monotonic=self._started_monotonic,
        )


class DebugTracer:
    def __init__(self, config: DebugTraceConfig) -> None:
        if not config.enabled or not config.output_dir:
            raise ValueError("DebugTracer requires an enabled config with output_dir.")
        self._config = config
        directory = _validate_output_dir(config.output_dir)
        self._writer = _SessionWriter(directory)
        self._closed = False
        self._next_tool_call_id = 1
        self._id_lock = threading.Lock()
        self._status = {
            "enabled": True,
            "output_configured": True,
            "writable": True,
        }
        atexit.register(self.close)

    @classmethod
    def from_config(cls, config: DebugTraceConfig) -> "DebugTracer":
        if not config.enabled:
            raise ValueError("DebugTracer.from_config requires enabled config.")
        return cls(config)

    def call(
        self,
        *,
        correlation_id: str,
        spec: "OperationSpec",
        arguments: Mapping[str, Any],
    ) -> DebugTraceSpan:
        started = time.monotonic()
        argument_shape = _project_argument_shape(arguments)
        with self._id_lock:
            tool_call_id = self._next_tool_call_id
            self._next_tool_call_id += 1
        return DebugTraceSpan(
            tracer=self,
            tool_call_id=tool_call_id,
            correlation_id=correlation_id,
            spec=spec,
            argument_shape=argument_shape,
            started_monotonic=started,
        )

    def status(self) -> Mapping[str, Any]:
        return dict(self._status)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()

    def _stop_writer(self, reason: str) -> None:
        self._writer._stop(reason)


def disabled_trace_status(*, output_configured: bool = False) -> dict[str, Any]:
    return {
        "enabled": False,
        "output_configured": output_configured,
        "writable": False,
    }


def create_tracer_from_env() -> tuple[DebugTracer | None, Mapping[str, Any]]:
    config = DebugTraceConfig.from_env()
    if not config.enabled:
        return None, disabled_trace_status(
            output_configured=bool(config.output_dir),
        )
    tracer = DebugTracer.from_config(config)
    return tracer, tracer.status()
