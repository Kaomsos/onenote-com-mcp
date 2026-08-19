"""Deterministic contracts for MCP runtime debug trace."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from local_onenote_mcp.debug_trace import (
    TraceEvent,
    DebugTraceConfig,
    DebugTracer,
    _project_argument_shape,
    create_tracer_from_env,
    disabled_trace_status,
)
from local_onenote_mcp.onenote_errors import OneNoteError
from local_onenote_mcp.services.errors import PartialFailure, PageTitleReadbackMismatch
from local_onenote_mcp.execution_context import current_correlation_id
from local_onenote_mcp.services.coordination import ReadWriteCoordinator
from local_onenote_mcp.services.errors import classify_error
from local_onenote_mcp.services.operation_runtime import (
    BackendCategory,
    CoordinationMode,
    OperationKind,
    OperationRegistry,
    OperationRuntime,
    OperationSpec,
    STRATEGIES,
    record_backend_call,
)
from local_onenote_mcp.tools.responses import caught

_COMMON_FIELDS = frozenset(
    {
        "recorded_at",
        "elapsed_seconds",
        "tool_call_id",
        "correlation_id",
        "tool",
        "event",
    }
)
_BACKEND_FIELDS = (
    "backend_call_id",
    "operation",
    "tool_call_id",
    "tool",
    "recorded_at",
    "elapsed_seconds",
    "backend_category",
    "correlation_id",
)
_TOOL_PREFIX = (
    "tool_call_id",
    "tool",
    "recorded_at",
    "elapsed_seconds",
    "event",
    "correlation_id",
)
_STALE_FIELDS = frozenset(
    {
        "schema_version",
        "phase",
        "runtime_stage",
        "backend_calls",
        "backend_call_index",
        "attempts",
        "replayed",
        "content_exposed",
    }
)


def _runtime_with_tracer(
    tmp_path: Path,
    *,
    handler=lambda _a: {"value": True},
    authorizer=None,
    platform_preflight=None,
    platform_preflight_policy: str = "none",
    finalizer=None,
    tracer: DebugTracer | None = None,
) -> tuple[OperationRuntime, DebugTracer]:
    if tracer is None:
        tracer = DebugTracer.from_config(
            DebugTraceConfig(enabled=True, output_dir=str(tmp_path))
        )
    registry = OperationRegistry()
    spec = OperationSpec(
        name="operation",
        category="test",
        kind=OperationKind.READ,
        capability="operation",
        coordination=CoordinationMode.SHARED,
        backend=BackendCategory.ONENOTE_COM,
        strategy="read",
        handler="tests.operation",
        platform_preflight_policy=platform_preflight_policy,
    )
    registry.register(
        spec,
        STRATEGIES["read"],
        handler,
        authorizer,
        platform_preflight,
    )
    runtime = OperationRuntime(
        registry,
        ReadWriteCoordinator(default_timeout_seconds=1.0),
        tracer=tracer,
        finalizer=finalizer or (lambda _execution: None),
    )
    return runtime, tracer


def _read_events(tracer: DebugTracer) -> list[dict]:
    lines = tracer._writer.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _tool_events(records: list[dict]) -> list[dict]:
    return [record for record in records if "event" in record]


def _backend_events(records: list[dict]) -> list[dict]:
    return [record for record in records if "backend_call_id" in record]


def _assert_event_shape(event: dict) -> None:
    assert _COMMON_FIELDS <= set(event)
    assert _STALE_FIELDS.isdisjoint(event)
    assert list(event)[: len(_TOOL_PREFIX)] == list(_TOOL_PREFIX)


def _assert_backend_shape(event: dict) -> None:
    for field in _BACKEND_FIELDS:
        assert field in event
    extra = set(event) - set(_BACKEND_FIELDS)
    assert extra <= {"read_reason"}
    if "read_reason" in event:
        from local_onenote_mcp.services.read_reasons import READ_REASONS

        assert event["read_reason"] in READ_REASONS
    assert "event" not in event
    assert _STALE_FIELDS.isdisjoint(event)


def test_debug_trace_disabled_has_zero_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "false")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", str(debug_dir))

    tracer, status = create_tracer_from_env()

    assert tracer is None
    assert status == disabled_trace_status(output_configured=True)
    assert "schema_version" not in status
    assert not debug_dir.exists()


def test_debug_trace_enabled_requires_absolute_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", "relative/path")
    with pytest.raises(ValueError, match="absolute path"):
        create_tracer_from_env()


def test_debug_trace_enabled_creates_missing_absolute_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debug_dir = tmp_path / "missing" / "debug"
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", str(debug_dir))

    tracer, status = create_tracer_from_env()

    assert debug_dir.is_dir()
    assert tracer is not None
    assert tracer._writer.path.parent == debug_dir
    assert status == tracer.status()
    assert "schema_version" not in status
    tracer.close()


def test_debug_trace_enabled_uses_and_creates_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debug_dir = tmp_path / "home" / ".onenote-mcp" / "debug-trace"
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.delenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", raising=False)
    monkeypatch.setattr(
        "local_onenote_mcp.debug_trace.default_debug_trace_dir",
        lambda: debug_dir,
    )

    tracer, status = create_tracer_from_env()

    assert debug_dir.is_dir()
    assert tracer is not None
    assert tracer._writer.path.parent == debug_dir
    assert status == tracer.status()
    tracer.close()


def test_debug_trace_enabled_rejects_explicit_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", "   ")

    with pytest.raises(ValueError, match="must not be empty"):
        create_tracer_from_env()


def test_debug_trace_fails_closed_when_directory_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocking_parent = tmp_path / "not-a-directory"
    blocking_parent.write_text("block", encoding="utf-8")
    debug_dir = blocking_parent / "debug"
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", str(debug_dir))

    with pytest.raises(ValueError, match="could not be created"):
        create_tracer_from_env()

    assert not debug_dir.exists()


def test_debug_trace_rejects_reparse_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    debug_dir = tmp_path / "debug"
    debug_dir.mkdir()
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_TRACE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MCP_DEBUG_DIR", str(debug_dir))

    original_lstat = os.lstat

    def fake_lstat(path: str | os.PathLike[str]):
        result = original_lstat(path)
        if Path(path) == debug_dir:
            return SimpleNamespace(
                st_file_attributes=getattr(result, "st_file_attributes", 0) | 0x400
            )
        return result

    monkeypatch.setattr(os, "lstat", fake_lstat)
    with pytest.raises(ValueError, match="reparse point"):
        create_tracer_from_env()


def test_successful_call_emits_expected_event_sequence(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(tmp_path)

    outcome = runtime.execute("operation", {"limit": 3})

    assert outcome.success is True
    events = _read_events(tracer)
    names = [event["event"] for event in events]
    assert names == [
        TraceEvent.TOOL_CALL_ENTERED.value,
        TraceEvent.TOOL_CALL_VALIDATED.value,
        TraceEvent.TOOL_CALL_AUTHORIZED.value,
        TraceEvent.TOOL_CALL_HANDLER_STARTED.value,
        TraceEvent.TOOL_CALL_FINALIZING.value,
        TraceEvent.TOOL_CALL_COMPLETED.value,
    ]
    for event in events:
        _assert_event_shape(event)
        assert event["tool_call_id"] == 1
        assert event["tool"] == "operation"
    entered = events[0]
    assert entered["operation_kind"] == OperationKind.READ.value
    assert entered["operation_strategy"] == "read"
    assert "argument_shape" not in entered
    validated = events[1]
    assert "keys" in validated["argument_shape"]
    assert "operation_kind" not in validated
    terminal = events[-1]
    assert terminal["summary"] == {
        "backend_call_count": 0,
        "attempts": 0,
        "replayed": False,
    }
    assert "backend_calls" not in terminal


def test_platform_preflight_events_only_when_policy_declared(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        platform_preflight_policy="onenote_gui_ready",
        platform_preflight=lambda _a: None,
    )

    runtime.execute("operation", {})

    names = [event["event"] for event in _read_events(tracer)]
    assert TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_STARTED.value in names
    assert TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_COMPLETED.value in names
    assert all("status" not in event for event in _read_events(tracer))


def test_authorization_rejection_emits_authorization_rejected(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        authorizer=lambda _a: (_ for _ in ()).throw(PermissionError("denied")),
    )

    outcome = runtime.execute("operation", {})

    assert outcome.success is False
    names = [event["event"] for event in _read_events(tracer)]
    assert TraceEvent.TOOL_CALL_AUTHORIZATION_REJECTED.value in names
    assert names[-1] == TraceEvent.TOOL_CALL_FAILED.value
    assert names.count(TraceEvent.TOOL_CALL_COMPLETED.value) == 0
    assert names.count(TraceEvent.TOOL_CALL_FAILED.value) == 1
    rejected = next(
        event
        for event in _read_events(tracer)
        if event["event"] == TraceEvent.TOOL_CALL_AUTHORIZATION_REJECTED.value
    )
    assert rejected["error"]["code"] == classify_error(PermissionError("denied")).code


def test_platform_preflight_failure_emits_failed_status(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        platform_preflight_policy="onenote_gui_ready",
        platform_preflight=lambda _a: (_ for _ in ()).throw(RuntimeError("preflight")),
    )

    outcome = runtime.execute("operation", {})

    assert outcome.success is False
    names = [event["event"] for event in _read_events(tracer)]
    assert TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_STARTED.value in names
    assert TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_FAILED.value in names
    assert TraceEvent.TOOL_CALL_PLATFORM_PREFLIGHT_COMPLETED.value not in names


def test_keyboard_interrupt_emits_cancelled_and_resets_context(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        handler=lambda _a: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.execute("operation", {})

    names = [event["event"] for event in _read_events(tracer)]
    assert names[-1] == TraceEvent.TOOL_CALL_CANCELLED.value
    assert names.count(TraceEvent.TOOL_CALL_CANCELLED.value) == 1
    assert current_correlation_id() is None
    cancelled = _read_events(tracer)[-1]
    assert "summary" in cancelled


def test_backend_dispatch_uses_spec_backend_and_filesystem_allowlist(tmp_path: Path) -> None:
    def handler(_arguments):
        record_backend_call("get_hierarchy")
        record_backend_call("filesystem:publish_target_exists")
        return {"value": True}

    runtime, tracer = _runtime_with_tracer(tmp_path, handler=handler)
    first = runtime.execute("operation", {})
    second = runtime.execute("operation", {})

    assert first.backend_calls == 2
    assert second.backend_calls == 2
    events = _read_events(tracer)
    backend_events = _backend_events(events)
    assert [event["backend_category"] for event in backend_events] == [
        BackendCategory.ONENOTE_COM.value,
        BackendCategory.FILESYSTEM.value,
        BackendCategory.ONENOTE_COM.value,
        BackendCategory.FILESYSTEM.value,
    ]
    assert [event["backend_call_id"] for event in backend_events] == [1, 2, 1, 2]
    assert [event["operation"] for event in backend_events] == [
        "get_hierarchy",
        "filesystem:publish_target_exists",
        "get_hierarchy",
        "filesystem:publish_target_exists",
    ]
    assert [event["tool_call_id"] for event in backend_events] == [1, 1, 2, 2]
    for event in backend_events:
        _assert_backend_shape(event)
        assert event["tool"] == "operation"
    for event in _tool_events(events):
        _assert_event_shape(event)
    rendered = json.dumps(backend_events)
    assert "secret" not in rendered
    terminals = [
        event
        for event in events
        if event.get("event") == TraceEvent.TOOL_CALL_COMPLETED.value
    ]
    assert [event["summary"]["backend_call_count"] for event in terminals] == [2, 2]


def test_backend_dispatch_records_allowlisted_read_reason(tmp_path: Path) -> None:
    from local_onenote_mcp.services.read_reasons import PLAN_CAPTURE, read_reason

    def handler(_arguments):
        with read_reason(PLAN_CAPTURE):
            record_backend_call("get_hierarchy")
        return {"value": True}

    runtime, tracer = _runtime_with_tracer(tmp_path, handler=handler)
    runtime.execute("operation", {})
    backend_events = _backend_events(_read_events(tracer))
    assert backend_events[0]["read_reason"] == PLAN_CAPTURE
    _assert_backend_shape(backend_events[0])


def test_argument_shape_projection_is_content_free() -> None:
    shape = _project_argument_shape(
        {
            "query": "secret search",
            "page_id": "{01234567-89AB-CDEF-0123-456789ABCDEF}",
            "items": ["a", "b"],
            "optional": None,
        }
    )

    rendered = json.dumps(shape)
    assert shape["keys"] == ["items", "optional", "page_id", "query"]
    assert shape["types"]["query"] == "str"
    assert shape["lengths"]["query"] == len("secret search")
    assert shape["is_none"]["optional"] is True
    assert "secret search" not in rendered
    assert "01234567" not in rendered


def test_sentinel_values_do_not_leak_into_trace_or_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "TOP_SECRET_NOTEBOOK_PATH\\page.one"
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        handler=lambda _a: (_ for _ in ()).throw(
            ValueError(f"failed for {sentinel}")
        ),
    )

    runtime.execute("operation", {"notebook_path": sentinel, "body": "<xml/>"})

    rendered = tracer._writer.path.read_text(encoding="utf-8")
    stderr = capsys.readouterr().err
    for blob in (rendered, stderr):
        assert sentinel not in blob
        assert "<xml/>" not in blob
        assert "failed for" not in blob


def test_writer_failure_is_contained_without_changing_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, tracer = _runtime_with_tracer(tmp_path)

    original_append = tracer._writer.append

    def failing_append(record):
        if record.get("event") == TraceEvent.TOOL_CALL_AUTHORIZED.value:
            raise OSError("disk full")
        return original_append(record)

    monkeypatch.setattr(tracer._writer, "append", failing_append)

    outcome = runtime.execute("operation", {})

    assert outcome.success is True
    stderr = capsys.readouterr().err
    assert "debug trace stopped" in stderr
    names = [event["event"] for event in _read_events(tracer)]
    assert names == [
        TraceEvent.TOOL_CALL_ENTERED.value,
        TraceEvent.TOOL_CALL_VALIDATED.value,
    ]


def test_concurrent_calls_have_unique_correlation_and_tool_call_ids(
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(2)
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        handler=lambda _a: (barrier.wait(timeout=1), {"value": True})[1],
    )
    results: list[str] = []

    def worker() -> None:
        runtime.execute("operation", {})
        correlation = current_correlation_id()
        results.append("unset" if correlation is None else correlation)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    events = _read_events(tracer)
    correlation_ids = {event["correlation_id"] for event in events}
    tool_call_ids = {event["tool_call_id"] for event in events}
    assert len(correlation_ids) == 2
    assert tool_call_ids == {1, 2}
    assert results == ["unset", "unset"]
    by_tool_call = {event["tool_call_id"]: event["correlation_id"] for event in events}
    assert len(by_tool_call) == 2


def test_classify_error_matches_caught_envelope_codes() -> None:
    assert classify_error(PermissionError("denied")).code == caught(
        PermissionError("denied")
    )["error"]["code"]
    assert classify_error(ValueError("bad")).code == caught(ValueError("bad"))["error"][
        "code"
    ]


def test_tracer_close_is_idempotent(tmp_path: Path) -> None:
    tracer = DebugTracer.from_config(
        DebugTraceConfig(enabled=True, output_dir=str(tmp_path))
    )
    tracer.close()
    tracer.close()


def test_finish_projection_drops_unlisted_outcome_fields(tmp_path: Path) -> None:
    sentinel = "TOP_SECRET_OUTCOME_VALUE"
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        handler=lambda _a: {
            "observed_outcome": sentinel,
            "retry_safety": sentinel,
            "recommended_action": sentinel,
        },
    )

    runtime.execute("operation", {})

    terminal = _read_events(tracer)[-1]
    assert terminal["event"] == TraceEvent.TOOL_CALL_COMPLETED.value
    assert terminal["observed_outcome"] == "unspecified"
    assert terminal["retry_safety"] == "unspecified"
    assert "recommended_action" not in terminal
    rendered = json.dumps(_read_events(tracer))
    assert sentinel not in rendered


def test_finalizer_failure_emits_finalizing_before_failed(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(
        tmp_path,
        finalizer=lambda _execution: (_ for _ in ()).throw(RuntimeError("finalize bug")),
    )

    outcome = runtime.execute("operation", {})

    assert outcome.success is False
    names = [event["event"] for event in _read_events(tracer)]
    assert names.index(TraceEvent.TOOL_CALL_FINALIZING.value) < names.index(
        TraceEvent.TOOL_CALL_FAILED.value
    )
    assert names.count(TraceEvent.TOOL_CALL_FAILED.value) == 1


def test_writer_capacity_is_enforced_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("local_onenote_mcp.debug_trace.MAX_SESSION_EVENTS", 3)
    monkeypatch.setattr("local_onenote_mcp.debug_trace.MAX_SESSION_BYTES", 512)
    tracer = DebugTracer.from_config(
        DebugTraceConfig(enabled=True, output_dir=str(tmp_path))
    )
    writer = tracer._writer
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait(timeout=1)
        writer.append({"backend_call_id": 1, "operation": "get_hierarchy"})

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 3
    assert writer._handle.closed is True


def test_writer_stop_closes_handle_and_blocks_further_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("local_onenote_mcp.debug_trace._DIAGNOSTIC_EMITTED", False)
    tracer = DebugTracer.from_config(
        DebugTraceConfig(enabled=True, output_dir=str(tmp_path))
    )
    writer = tracer._writer

    def failing_write(_line: str) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(writer._handle, "write", failing_write)

    writer.append({"event": "tool_call.validated"})

    assert writer._handle.closed is True
    before = writer.path.read_text(encoding="utf-8")
    writer.append({"event": "tool_call.authorized"})
    assert writer.path.read_text(encoding="utf-8") == before
    stderr = capsys.readouterr().err
    assert stderr.count("debug trace stopped") == 1
    tracer.close()


def test_classify_error_projects_unknown_subclasses_to_stable_types() -> None:
    class UnknownOneNoteError(OneNoteError):
        code = "unknown_onenote"

    class UnknownPartialFailure(PartialFailure):
        pass

    assert classify_error(UnknownOneNoteError("x")).error_type == "OneNoteError"
    assert classify_error(UnknownPartialFailure("x")).error_type == "PartialFailure"
    assert (
        classify_error(PageTitleReadbackMismatch("x")).error_type
        == "PageTitleReadbackMismatch"
    )


def test_caught_rejects_duck_typed_public_details() -> None:
    class FakeError(RuntimeError):
        def public_details(self) -> dict[str, str]:
            return {"secret": "must-not-appear"}

    class BrokenPublicDetails(RuntimeError):
        def public_details(self) -> dict[str, str]:
            raise RuntimeError("broken")

    fake = caught(FakeError("fail"))
    assert fake["error"]["code"] == "backend_error"
    assert "secret" not in json.dumps(fake)

    broken = caught(BrokenPublicDetails("fail"))
    assert broken["error"]["code"] == "backend_error"


def test_sequential_calls_increment_tool_call_id(tmp_path: Path) -> None:
    runtime, tracer = _runtime_with_tracer(tmp_path)
    runtime.execute("operation", {})
    runtime.execute("operation", {})

    events = _read_events(tracer)
    first = {event["event"] for event in events if event["tool_call_id"] == 1}
    second = {event["event"] for event in events if event["tool_call_id"] == 2}
    assert TraceEvent.TOOL_CALL_ENTERED.value in first
    assert TraceEvent.TOOL_CALL_COMPLETED.value in first
    assert TraceEvent.TOOL_CALL_ENTERED.value in second
    assert TraceEvent.TOOL_CALL_COMPLETED.value in second
    assert {event["tool_call_id"] for event in events} == {1, 2}
