"""Content-free helpers for the Negative Move Page cross-second dateTime scenario."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from local_onenote_mcp.page.datetime_compare import utc_second

from ...runtime import ExpectedNegativeOutcome, InvariantFailure, RunnerFailure
from .move_page_evidence import partial_move_details


TRIGGER_TOOL = "move_page"
TRIGGER_READ_REASON = "topology_verification"
TRIGGER_OPERATION = "get_hierarchy"
SOURCE_DRIFT_REASON = "source_drift_revalidation"
DATETIME_VERIFICATION_REASONS = frozenset(
    {"final_target_readback", "final_source_revalidation"}
)
SETTER_ROUTE = "update_hierarchy"
CASE_NAME = "datetime-drift-negative"
DRIFT_TARGET_KEY = "subtree_child"
CREATE_OPERATIONS = frozenset({"create_new_page"})
DELETE_OPERATIONS = frozenset({"delete_hierarchy"})
TRIGGER_POLL_INTERVAL_SECONDS = 0.05
TRIGGER_TIMEOUT_SECONDS = 60.0
READBACK_ATTEMPTS = 3
READBACK_POLL_INTERVAL_SECONDS = 0.05
FRESH_ONLY_REASON = (
    "Negative Move Page dateTime-drift scenario is fresh-only so the "
    "source subtree is not pre-copied from cache."
)


def next_utc_second(value: str | None) -> str:
    """Return the next whole UTC second in ``YYYY-MM-DDTHH:MM:SSZ`` form."""

    current = utc_second(value)
    if current is None:
        raise InvariantFailure("Page dateTime cannot be normalized to a UTC second.")
    parsed = datetime.fromisoformat(current.replace("Z", "+00:00"))
    advanced = parsed + timedelta(seconds=1)
    return advanced.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_exact_trigger_event(record: Mapping[str, Any]) -> bool:
    """Accept only the exact Move topology-verification hierarchy read."""

    return (
        record.get("tool") == TRIGGER_TOOL
        and record.get("read_reason") == TRIGGER_READ_REASON
        and record.get("operation") == TRIGGER_OPERATION
    )


def is_datetime_verification_event(record: Mapping[str, Any]) -> bool:
    return (
        record.get("tool") == TRIGGER_TOOL
        and record.get("operation") == "get_page_content"
        and record.get("read_reason") in DATETIME_VERIFICATION_REASONS
    )


def is_source_drift_event(record: Mapping[str, Any]) -> bool:
    return (
        record.get("tool") == TRIGGER_TOOL
        and record.get("read_reason") == SOURCE_DRIFT_REASON
    )


def session_trace_paths(trace_dir: Path) -> list[Path]:
    if not trace_dir.exists():
        return []
    return sorted(
        path
        for path in trace_dir.iterdir()
        if path.is_file() and path.name.startswith("session-") and path.suffix == ".jsonl"
    )


def load_trace_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def scan_trace_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Project content-free phase order without counting backend calls."""

    datetime_verified = False
    trigger_index: int | None = None
    source_drift_index: int | None = None
    for index, record in enumerate(records):
        if is_datetime_verification_event(record):
            datetime_verified = True
        if (
            datetime_verified
            and trigger_index is None
            and is_exact_trigger_event(record)
        ):
            trigger_index = index
        if source_drift_index is None and is_source_drift_event(record):
            source_drift_index = index
    return {
        "datetime_verification_observed": datetime_verified,
        "trigger_observed": trigger_index is not None,
        "trigger_index": trigger_index,
        "source_drift_observed": source_drift_index is not None,
        "source_drift_index": source_drift_index,
        "trigger_before_source_drift": (
            trigger_index is not None
            and (source_drift_index is None or trigger_index < source_drift_index)
        ),
    }


def require_debug_trace_ready(health: Mapping[str, Any] | None) -> dict[str, Any]:
    debug_trace = health.get("debug_trace") if isinstance(health, Mapping) else None
    if not isinstance(debug_trace, Mapping):
        raise RunnerFailure("Negative Move Page dateTime-drift scenario requires debug_trace health.")
    status = {
        "enabled": debug_trace.get("enabled") is True,
        "output_configured": debug_trace.get("output_configured") is True,
        "writable": debug_trace.get("writable") is True,
    }
    if not all(status.values()):
        raise RunnerFailure(
            "Negative Move Page dateTime-drift scenario requires "
            "debug_trace.enabled/output_configured/writable."
        )
    return status


def require_source_drift_absent(trace_dir: Path) -> dict[str, Any]:
    session = require_unique_session_trace(trace_dir)
    scan = scan_trace_records(load_trace_records(session))
    if scan["source_drift_observed"]:
        raise InvariantFailure(
            "source_drift_revalidation appeared before the datetime-drift write completed."
        )
    return scan


def count_bridge_operations(records: list[Mapping[str, Any]]) -> dict[str, int]:
    create_count = 0
    delete_count = 0
    for record in records:
        operation = str(record.get("operation") or "")
        if operation in CREATE_OPERATIONS:
            create_count += 1
        if operation in DELETE_OPERATIONS:
            delete_count += 1
    return {
        "create_new_page": create_count,
        "delete_hierarchy": delete_count,
    }


def count_move_submissions(records: list[Mapping[str, Any]]) -> int:
    return sum(1 for record in records if record.get("tool") == TRIGGER_TOOL)


def inspect_copy_only_envelope(envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    details = partial_move_details(envelope)
    report = details.get("copy_report")
    report = report if isinstance(report, Mapping) else {}
    page_results = [
        value for value in report.get("page_results", ()) if isinstance(value, Mapping)
    ]
    statuses = []
    rewritten = False
    for result in page_results:
        date_time = result.get("date_time")
        status = date_time.get("status") if isinstance(date_time, Mapping) else None
        statuses.append(status)
        if status == "source_drifted":
            rewritten = True
    id_map = report.get("id_map")
    id_map = dict(id_map) if isinstance(id_map, Mapping) else {}
    created_ids = [str(value) for value in details.get("created_ids", ()) if value]
    mapped_ids = [str(value) for value in id_map.values() if value]
    return {
        "error_code": details.get("code"),
        "outcome": details.get("outcome"),
        "source_deleted": details.get("source_deleted"),
        "verified": report.get("verified"),
        "lossless": report.get("lossless"),
        "copy_contract_satisfied": report.get("copy_contract_satisfied"),
        "page_date_time_statuses": statuses,
        "report_rewritten_source_drifted": rewritten,
        "created_ids": created_ids,
        "id_map": id_map,
        "created_ids_match_id_map": created_ids == mapped_ids,
    }


def require_copy_only_envelope(envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    inspected = inspect_copy_only_envelope(envelope)
    if (
        inspected["error_code"] != "partial_failure"
        or inspected["outcome"] != "copy_only"
        or inspected["source_deleted"] is not False
        or inspected["verified"] is not True
        or inspected["lossless"] is not True
        or inspected["copy_contract_satisfied"] is not True
        or not inspected["page_date_time_statuses"]
        or any(status != "verified" for status in inspected["page_date_time_statuses"])
        or inspected["report_rewritten_source_drifted"]
        or not inspected["created_ids_match_id_map"]
    ):
        raise InvariantFailure(
            "Negative Move Page dateTime-drift scenario did not return the expected copy_only envelope."
        )
    return inspected


def require_unique_session_trace(trace_dir: Path) -> Path:
    sessions = session_trace_paths(trace_dir)
    if len(sessions) != 1:
        raise InvariantFailure(
            "Negative Move Page dateTime-drift scenario requires exactly one debug-trace session."
        )
    return sessions[0]


async def wait_for_datetime_drift_trigger(
    trace_dir: Path,
    move_task: asyncio.Task[Any],
    *,
    timeout_seconds: float = TRIGGER_TIMEOUT_SECONDS,
    poll_interval_seconds: float = TRIGGER_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Wait for the post-verification topology trigger without counting backend calls."""

    sleeper = sleep or asyncio.sleep
    now = clock or asyncio.get_running_loop().time
    deadline = now() + timeout_seconds
    while now() < deadline:
        if move_task.done():
            raise InvariantFailure(
                "Move Page finished before the datetime-drift trigger was observed."
            )
        sessions = session_trace_paths(trace_dir)
        if len(sessions) > 1:
            raise InvariantFailure(
                "Negative Move Page dateTime-drift scenario observed more than one debug-trace session."
            )
        if len(sessions) == 1:
            records = load_trace_records(sessions[0])
            scan = scan_trace_records(records)
            if scan["source_drift_observed"] and not scan["trigger_observed"]:
                raise InvariantFailure(
                    "source_drift_revalidation appeared before the datetime-drift trigger."
                )
            if scan["trigger_observed"]:
                if not scan["trigger_before_source_drift"]:
                    raise InvariantFailure(
                        "The datetime-drift trigger was not recorded before source_drift_revalidation."
                    )
                return {
                    "session_path": str(sessions[0]),
                    "scan": scan,
                    "records": records,
                }
        await sleeper(poll_interval_seconds)
    if move_task.done():
        raise InvariantFailure(
            "Move Page finished before the datetime-drift trigger was observed."
        )
    raise InvariantFailure("Timed out waiting for the datetime-drift trigger.")


async def apply_datetime_drift_once(
    client: Any,
    *,
    notebook_id: str,
    page_id: str,
    expected_parent_id: Any,
    expected_hierarchy_modified: Any,
    expected_date_time: str,
    next_second: str,
) -> dict[str, Any]:
    """Dispatch exactly one validation-only dateTime write and refuse retries."""

    dispatch = await client.call_tool(
        "set_verified_page_datetime",
        {
            "notebook_id": notebook_id,
            "page_id": page_id,
            "expected_parent_id": expected_parent_id,
            "expected_hierarchy_modified": expected_hierarchy_modified,
            "expected_date_time": expected_date_time,
            "route": SETTER_ROUTE,
            "date_time": next_second,
        },
        retry_read=False,
    )
    status = str(dispatch.get("status", "state_uncertain"))
    if status == "precondition_drifted":
        raise InvariantFailure(
            "Verified Page dateTime setter refused because a precondition drifted."
        )
    if status == "write_failed":
        raise InvariantFailure("Verified Page dateTime setter write failed.")
    if status != "dispatched" or dispatch.get("mutation_dispatched") is not True:
        raise InvariantFailure("Verified Page dateTime setter did not dispatch exactly once.")
    if dispatch.get("mutation_attempts", 1) != 1 or dispatch.get("mutation_replayed") is True:
        raise InvariantFailure("Verified Page dateTime setter retried or replayed.")
    return {
        "status": status,
        "mutation_dispatched": True,
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "route": SETTER_ROUTE,
        "bridge_operation": dispatch.get("bridge_operation"),
    }


async def confirm_next_utc_second(
    client: Any,
    *,
    notebook_id: str,
    page_id: str,
    expected_second: str,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    attempts: int = READBACK_ATTEMPTS,
    poll_interval_seconds: float = READBACK_POLL_INTERVAL_SECONDS,
) -> str:
    """Read the exact Page dateTime a bounded number of times without rewriting."""

    sleeper = sleep or asyncio.sleep
    last_value: str | None = None
    for attempt in range(attempts):
        result = await client.call_tool(
            "read_verified_page_datetime",
            {
                "notebook_id": notebook_id,
                "page_id": page_id,
                "route": SETTER_ROUTE,
            },
            retry_read=False,
        )
        if result.get("status") != "observed":
            raise InvariantFailure("Verified Page dateTime readback was not observable.")
        last_value = str(result.get("date_time") or "")
        if utc_second(last_value) == expected_second:
            return last_value
        if attempt + 1 < attempts:
            await sleeper(poll_interval_seconds)
    raise InvariantFailure(
        "Verified Page dateTime readback did not settle on the next UTC second."
    )


def require_source_and_target_layout(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    source_ids: list[str],
    id_map: Mapping[str, str],
    destination_section_id: str,
    drift_source_id: str,
    expected_source_second: str,
    expected_target_second: str,
) -> dict[str, Any]:
    after_by_id = {str(item["id"]): item for item in after.get("items", [])}
    missing_sources = [page_id for page_id in source_ids if page_id not in after_by_id]
    if missing_sources:
        raise InvariantFailure("datetime-drift-negative evidence is refused because a source Page is missing.")
    target_ids = [str(id_map[page_id]) for page_id in source_ids]
    missing_targets = [
        page_id
        for page_id in target_ids
        if page_id not in after_by_id
        or str(after_by_id[page_id].get("section_id")) != destination_section_id
    ]
    if missing_targets:
        raise InvariantFailure("datetime-drift-negative evidence is refused because a mapped target is missing.")
    duplicate_targets = [
        page_id
        for page_id, item in after_by_id.items()
        if item.get("resource_type") == "page"
        and str(item.get("section_id")) == destination_section_id
        and page_id not in set(target_ids)
        and page_id not in {str(value.get("id")) for value in before.get("items", [])}
    ]
    if duplicate_targets:
        raise InvariantFailure(
            "datetime-drift-negative evidence is refused because destination contains a duplicate target."
        )
    before_hashes = before.get("page_hashes")
    after_hashes = after.get("page_hashes")
    if not isinstance(before_hashes, Mapping) or not isinstance(after_hashes, Mapping):
        raise InvariantFailure("Negative Move Page dateTime-drift evidence is missing source hashes.")
    if any(before_hashes.get(page_id) != after_hashes.get(page_id) for page_id in source_ids):
        raise InvariantFailure("Negative Move Page dateTime-drift changed a source Page content hash.")
    before_seconds = before.get("page_datetime_seconds")
    after_seconds = after.get("page_datetime_seconds")
    if not isinstance(before_seconds, Mapping) or not isinstance(after_seconds, Mapping):
        raise InvariantFailure("Negative Move Page dateTime-drift evidence is missing Page seconds.")
    if after_seconds.get(drift_source_id) != expected_source_second:
        raise InvariantFailure("Source Page dateTime did not remain on the drifted UTC second.")
    target_id = str(id_map[drift_source_id])
    if after_seconds.get(target_id) != expected_target_second:
        raise InvariantFailure("Target Page dateTime did not remain on the frozen planning second.")
    return {
        "source_present": True,
        "targets_present": True,
        "duplicate_targets": False,
        "source_hashes_unchanged": True,
        "source_utc_second": expected_source_second,
        "target_utc_second": expected_target_second,
    }


def require_backend_counts(
    *,
    delete_count: int,
    create_count: int,
    expected_creates: int,
    move_submissions: int,
) -> dict[str, int]:
    if delete_count != 0:
        raise InvariantFailure(
            "datetime-drift-negative evidence is refused because a delete_hierarchy call occurred."
        )
    if create_count != expected_creates:
        raise InvariantFailure(
            "Negative Move Page dateTime-drift created a different number of target Pages than the source scope."
        )
    if move_submissions != 1:
        raise InvariantFailure("Move mutation was submitted more than once or replayed.")
    return {
        "delete_hierarchy": delete_count,
        "create_new_page": create_count,
        "move_page_submissions": move_submissions,
    }


def build_negative_gate_evidence(
    *,
    source_ids: list[str],
    drift_source_id: str,
    id_map: Mapping[str, str],
    original_utc_second: str,
    drifted_utc_second: str,
    trigger_scan: Mapping[str, Any],
    setter: Mapping[str, Any],
    envelope: Mapping[str, Any],
    layout: Mapping[str, Any],
    backend: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case": CASE_NAME,
        "negative_gate_verified": True,
        "source_page_ids": list(source_ids),
        "drift_source_page_id": drift_source_id,
        "target_page_ids": [str(id_map[page_id]) for page_id in source_ids],
        "original_utc_second": original_utc_second,
        "drifted_utc_second": drifted_utc_second,
        "trigger": {
            "tool": TRIGGER_TOOL,
            "read_reason": TRIGGER_READ_REASON,
            "operation": TRIGGER_OPERATION,
            "datetime_verification_observed": trigger_scan.get(
                "datetime_verification_observed"
            ),
            "trigger_observed": trigger_scan.get("trigger_observed"),
            "source_drift_observed_before_write": False,
            "trigger_before_source_drift": trigger_scan.get("trigger_before_source_drift"),
        },
        "setter": {
            "status": setter.get("status"),
            "count": 1,
            "mutation_dispatched": setter.get("mutation_dispatched"),
            "mutation_attempts": setter.get("mutation_attempts"),
            "mutation_replayed": setter.get("mutation_replayed"),
            "route": setter.get("route"),
        },
        "copy_report": {
            "verified": envelope.get("verified"),
            "lossless": envelope.get("lossless"),
            "copy_contract_satisfied": envelope.get("copy_contract_satisfied"),
            "page_date_time_statuses": list(envelope.get("page_date_time_statuses", ())),
            "report_rewritten_source_drifted": envelope.get(
                "report_rewritten_source_drifted"
            ),
        },
        "move": {
            "error_code": envelope.get("error_code"),
            "outcome": envelope.get("outcome"),
            "source_deleted": envelope.get("source_deleted"),
            "created_ids_match_id_map": envelope.get("created_ids_match_id_map"),
        },
        "existence": {
            "source_present": layout.get("source_present"),
            "targets_present": layout.get("targets_present"),
            "duplicate_targets": layout.get("duplicate_targets"),
            "source_hashes_unchanged": layout.get("source_hashes_unchanged"),
            "source_utc_second": layout.get("source_utc_second"),
            "target_utc_second": layout.get("target_utc_second"),
        },
        "backend": dict(backend),
        "content_exposed": False,
    }


def write_negative_gate_and_raise_expected_outcome(
    write: Callable[[dict[str, Any]], None],
    evidence: Mapping[str, Any],
    original_error: BaseException,
    *,
    evidence_path: Path,
) -> None:
    if evidence.get("negative_gate_verified") is not True:
        raise InvariantFailure("datetime-drift-negative evidence is incomplete.")
    write(dict(evidence))
    raise ExpectedNegativeOutcome(
        str(original_error),
        evidence_path=evidence_path,
        summary=(
            "Move source dateTime drift returned copy_only and blocked source deletion."
        ),
        original_error=original_error,
    ) from original_error


def dry_run_datetime_drift_projection(*, run_dir: Path) -> dict[str, Any]:
    return {
        "datetime_drift_negative": True,
        "trigger": {
            "tool": TRIGGER_TOOL,
            "read_reason": TRIGGER_READ_REASON,
            "operation": TRIGGER_OPERATION,
            "after": sorted(DATETIME_VERIFICATION_REASONS),
            "before": SOURCE_DRIFT_REASON,
        },
        "cross_second_write": {
            "count": 1,
            "route": SETTER_ROUTE,
            "retries": 0,
            "replayed": False,
        },
        "expected_outcome": "copy_only",
        "real_exit": "nonzero",
        "result_json_passed": False,
        "failure_site_preserved": True,
        "debug_trace": {
            "enabled": True,
            "output_dir": str((run_dir / "scenario-mcp" / "debug-trace").resolve()),
            "directory_created": False,
        },
        "sleep_performed": False,
        "directory_created": False,
        "mcp_started": False,
        "gui_state_read": False,
    }


def dry_run_datetime_drift_steps() -> list[dict[str, Any]]:
    return [
        {
            "step": "observe-debug-trace-trigger",
            "trust_boundary": "content-free debug trace observer",
            "target": (
                "first move_page/topology_verification/get_hierarchy after "
                "final dateTime verification"
            ),
            "sleep_performed": False,
            "directory_created": False,
            "mcp_started": False,
            "gui_state_read": False,
        },
        {
            "step": "write-source-datetime-plus-one-utc-second",
            "trust_boundary": "harness _internal_bridge validation-only capability",
            "allowed_operations": ["set_verified_page_datetime"],
            "target": "exact subtree child Page root dateTime +1s",
            "mutation_retries": 0,
            "replayed": False,
        },
        {
            "step": "expect-copy-only-and-nonzero-exit",
            "trust_boundary": "strict Move copy_only is not scenario success",
            "expected_outcome": "copy_only",
            "real_exit": "nonzero",
            "result_json_passed": False,
            "failure_site_preserved": True,
            "source_deleted": False,
        },
    ]


__all__ = [
    "CASE_NAME",
    "DRIFT_TARGET_KEY",
    "FRESH_ONLY_REASON",
    "SETTER_ROUTE",
    "SOURCE_DRIFT_REASON",
    "TRIGGER_OPERATION",
    "TRIGGER_READ_REASON",
    "TRIGGER_TOOL",
    "apply_datetime_drift_once",
    "build_negative_gate_evidence",
    "confirm_next_utc_second",
    "count_bridge_operations",
    "count_move_submissions",
    "dry_run_datetime_drift_projection",
    "dry_run_datetime_drift_steps",
    "inspect_copy_only_envelope",
    "is_exact_trigger_event",
    "load_trace_records",
    "next_utc_second",
    "require_backend_counts",
    "require_copy_only_envelope",
    "require_debug_trace_ready",
    "require_source_and_target_layout",
    "require_source_drift_absent",
    "require_unique_session_trace",
    "scan_trace_records",
    "session_trace_paths",
    "wait_for_datetime_drift_trigger",
    "write_negative_gate_and_raise_expected_outcome",
]
