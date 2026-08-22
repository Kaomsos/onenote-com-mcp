"""Isolated cross-second Page Move negative validation."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from ..mcp_stdio_client import (
    ClientFailure,
    MCPStdioClient,
    MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_POLICY,
    scenario_client,
)
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.config import MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS
from .common.copy_invariants import expected_copy_source_items
from .common.copy_runtime import call_with_result_evidence
from .common.datetime_drift_negative import (
    CASE_NAME,
    DRIFT_TARGET_KEY,
    FRESH_ONLY_REASON,
    SETTER_ROUTE,
    apply_datetime_drift_once,
    build_negative_gate_evidence,
    confirm_next_utc_second,
    count_bridge_operations,
    count_move_submissions,
    load_trace_records,
    next_utc_second,
    require_backend_counts,
    require_copy_only_envelope,
    require_debug_trace_ready,
    require_source_and_target_layout,
    require_source_drift_absent,
    wait_for_datetime_drift_trigger,
    write_negative_gate_and_raise_expected_outcome,
)
from .common.move_page_snapshot import capture_move_page_bundle
from .common.registry import SCENARIO_REGISTRY
from .common.specs import get_scenario_spec
from .fixture_recipes.move_page import NEGATIVE_RECIPE


SCENARIO_NAME = "negative-move-page-datetime-drift"


async def _execute_negative_move_page_datetime_drift(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    if options.use_cache:
        raise RunnerFailure(f"{FRESH_ONLY_REASON}; remove --use-cache.")
    validate_manifest_notebook(manifest, args.notebook_name)
    notebooks = manifest.get("notebooks")
    if not isinstance(notebooks, dict) or set(notebooks) != {"destination", "source"}:
        raise RunnerFailure("Negative Move Page requires exact source/destination Notebook roles.")
    spec = get_scenario_spec(SCENARIO_NAME)
    cases = spec.execution_contract.get("cases")
    if not isinstance(cases, list) or len(cases) != 1:
        raise RunnerFailure("Negative Move Page requires the subtree source only.")
    case = cases[0]
    if str(case.get("name")) != "cross-notebook-subtree":
        raise RunnerFailure("Negative Move Page must use the declared subtree source.")
    destination = resolve_manifest_item(manifest, "destination_section")
    source = resolve_manifest_item(manifest, str(case["source_key"]))
    drift_page = resolve_manifest_item(manifest, DRIFT_TARGET_KEY)
    out = scenario_dir(options.run_dir, SCENARIO_NAME)
    debug_trace_dir = getattr(client, "debug_trace_dir", None) if client is not None else None
    if debug_trace_dir is None:
        debug_trace_dir = options.run_dir / "scenario-mcp" / "debug-trace"
    async with scenario_client(
        client,
        policy=MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_POLICY,
        allowed_tools=MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
        debug_trace_dir=debug_trace_dir,
    ) as active_client:
        health = await active_client.call_tool("health_check", {}, retry_read=False)
        debug_trace = require_debug_trace_ready(health)
        if getattr(active_client, "debug_trace_dir", None) is None:
            raise RunnerFailure("Negative Move Page requires a configured debug-trace directory.")
        debug_trace_dir = active_client.debug_trace_dir
        before = await capture_move_page_bundle(active_client, notebooks)
        write_json(out / "before.json", before)
        current_source = find_snapshot_item(before, str(source["id"]))
        current_child = find_snapshot_item(before, str(drift_page["id"]))
        if current_source is None or current_child is None:
            raise RunnerFailure("Negative Move Page source subtree is missing before Move.")
        selected = expected_copy_source_items(before, str(current_source["id"]), True)
        expected_source_ids = [str(item["id"]) for item in selected]
        if str(current_child["id"]) not in expected_source_ids:
            raise RunnerFailure("Negative Move Page child is outside the subtree source.")
        destination_title = display_name(current_source)
        write_json(
            out / "datetime-drift-negative-plan.json",
            {
                "case": CASE_NAME,
                "source_page_id": str(current_source["id"]),
                "drift_source_page_id": str(current_child["id"]),
                "destination_section_id": str(destination["id"]),
                "include_subpages": True,
                "source_page_count": len(expected_source_ids),
                "trigger": dict(spec.execution_contract["trigger"]),
                "expected_outcome": "copy_only",
                "real_exit": "nonzero",
                "result_json_passed": False,
                "debug_trace": debug_trace,
                "content_exposed": False,
            },
        )
        observed = await active_client.call_tool(
            "read_verified_page_datetime",
            {
                "notebook_id": str(notebooks["source"]["id"]),
                "page_id": str(current_child["id"]),
                "route": SETTER_ROUTE,
            },
            retry_read=False,
        )
        if observed.get("status") != "observed" or not observed.get("date_time"):
            raise InvariantFailure("Negative Move Page source dateTime was not observable.")
        original_date_time = str(observed["date_time"])
        from local_onenote_mcp.page.datetime_compare import utc_second

        frozen_second = utc_second(original_date_time)
        if frozen_second is None:
            raise InvariantFailure("Negative Move Page source dateTime is not a UTC second.")
        next_second = next_utc_second(original_date_time)
        audit_path = active_client.run_dir / "bridge-calls.jsonl"
        calls_path = active_client.run_dir / "calls.jsonl"
        audit_cursor = len(load_trace_records(audit_path)) if audit_path.exists() else 0
        move_arguments = {
            "page_id": current_source["id"],
            "destination_section_id": destination["id"],
            "expected_title": display_name(current_source),
            "expected_section_id": current_source["section_id"],
            "expected_modified": current_source.get("modified"),
            "destination_title": destination_title,
            "include_subpages": True,
        }
        move_task = asyncio.create_task(
            call_with_result_evidence(
                active_client,
                "move_page",
                move_arguments,
                out / "copy-result-datetime-drift-negative.json",
            )
        )
        try:
            trigger = await wait_for_datetime_drift_trigger(debug_trace_dir, move_task)
            if move_task.done():
                raise InvariantFailure(
                    "Move Page finished before the datetime-drift write completed."
                )
            setter = await apply_datetime_drift_once(
                active_client,
                notebook_id=str(notebooks["source"]["id"]),
                page_id=str(current_child["id"]),
                expected_parent_id=current_child.get("parent_id"),
                expected_hierarchy_modified=current_child.get("modified"),
                expected_date_time=original_date_time,
                next_second=next_second,
            )
            readback = await confirm_next_utc_second(
                active_client,
                notebook_id=str(notebooks["source"]["id"]),
                page_id=str(current_child["id"]),
                expected_second=next_second,
            )
            trigger_scan = require_source_drift_absent(debug_trace_dir)
            trigger_scan = {
                **trigger["scan"],
                **trigger_scan,
                "source_drift_observed_before_write": False,
            }
            write_json(
                out / "datetime-drift-trigger.json",
                {
                    "tool": "move_page",
                    "read_reason": "topology_verification",
                    "operation": "get_hierarchy",
                    "datetime_verification_observed": True,
                    "source_drift_observed_before_write": False,
                    "setter_status": setter["status"],
                    "setter_count": 1,
                    "readback_utc_second": utc_second(readback),
                    "content_exposed": False,
                },
            )
        except BaseException:
            if not move_task.done():
                move_task.cancel()
                try:
                    await move_task
                except (asyncio.CancelledError, ClientFailure, InvariantFailure, RunnerFailure):
                    pass
            raise
        try:
            await move_task
        except ClientFailure as exc:
            original_error = exc
        else:
            raise InvariantFailure(
                "Negative Move Page expected copy_only, but Move succeeded."
            )
        envelope = require_copy_only_envelope(original_error.envelope)
        after = await capture_move_page_bundle(active_client, notebooks)
        write_json(out / "after.json", after)
        layout = require_source_and_target_layout(
            before=before,
            after=after,
            source_ids=expected_source_ids,
            id_map=envelope["id_map"],
            destination_section_id=str(destination["id"]),
            drift_source_id=str(current_child["id"]),
            expected_source_second=next_second,
            expected_target_second=frozen_second,
        )
        audit_records = load_trace_records(audit_path)[audit_cursor:] if audit_path.exists() else []
        backend = require_backend_counts(
            delete_count=count_bridge_operations(audit_records)["delete_hierarchy"],
            create_count=count_bridge_operations(audit_records)["create_new_page"],
            expected_creates=len(expected_source_ids),
            move_submissions=count_move_submissions(
                load_trace_records(calls_path) if calls_path.exists() else []
            ),
        )
        evidence = build_negative_gate_evidence(
            source_ids=expected_source_ids,
            drift_source_id=str(current_child["id"]),
            id_map=envelope["id_map"],
            original_utc_second=frozen_second,
            drifted_utc_second=next_second,
            trigger_scan=trigger_scan,
            setter=setter,
            envelope=envelope,
            layout=layout,
            backend=backend,
        )
        evidence_path = out / "datetime-drift-negative.json"
        write_negative_gate_and_raise_expected_outcome(
            lambda payload: write_json(evidence_path, payload),
            evidence,
            original_error,
            evidence_path=evidence_path,
        )
    raise AssertionError("Negative Move Page must raise its verified negative outcome.")


@SCENARIO_REGISTRY.register
class NegativeMovePageDatetimeDriftScenario(Scenario):
    name = SCENARIO_NAME
    fixture_recipe = NEGATIVE_RECIPE
    help_text = (
        "GATED: prove a cross-second source dateTime drift blocks Page Move source "
        "deletion after a verified Copy."
    )
    timeout_default = 1_800
    included_in_all = False
    worksite_dry_run_action = "preserve-negative-move-copy-only-evidence"

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        del fixture_result
        return await _execute_negative_move_page_datetime_drift(
            args,
            options,
            manifest,
            client=client,
        )


__all__ = ["NegativeMovePageDatetimeDriftScenario", "SCENARIO_NAME"]
