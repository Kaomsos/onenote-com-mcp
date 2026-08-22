"""Human-gated smoke check for the verified Page ``dateTime`` capability."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, TIMESTAMP_FIDELITY_POLICY, scenario_client
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
    capture_snapshot,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    write_json,
)
from ..timestamp_fidelity import compare_timestamp
from .base import Scenario
from .common.config import TIMESTAMP_FIDELITY_TOOLS
from .common.registry import SCENARIO_REGISTRY
from .common.report import render_report
from .fixture_recipes.timestamp_fidelity import RECIPE


# The 2026-08-22 human-gated run proved Page ``dateTime`` persists at seconds,
# while fractional seconds are truncated.  Keep this smoke input at that exact
# proven precision; it is not a general timestamp-fidelity promise.
PROBE_DATE_TIME = "2020-02-03T04:05:06+08:00"
_PROBE_SPECS = (
    ("page_hierarchy", "page_hierarchy_target", "update_hierarchy", "hierarchy"),
    ("page_content", "page_content_target", "update_page_content", "page_content"),
)
_IDENTITY_FIELDS = (
    "resource_type",
    "id",
    "name",
    "title",
    "parent_id",
    "notebook_id",
    "section_id",
    "parent_page_id",
    "page_level",
    "order",
)


def _assert_safety(before: dict[str, Any], after: dict[str, Any]) -> None:
    if snapshot_ids(before) != snapshot_ids(after):
        raise InvariantFailure("Page dateTime smoke check changed hierarchy object IDs.")
    before_by_id = {str(item["id"]): item for item in before.get("items", [])}
    after_by_id = {str(item["id"]): item for item in after.get("items", [])}
    for object_id, previous in before_by_id.items():
        current = after_by_id.get(object_id)
        if current is None or any(
            current.get(field) != previous.get(field) for field in _IDENTITY_FIELDS
        ):
            raise InvariantFailure(
                "Page dateTime smoke check changed resource identity, topology, order, or display fields."
            )
    for field in (
        "page_body_hashes",
        "page_semantic_content_identities",
        "page_objects",
    ):
        if before.get(field, {}) != after.get(field, {}):
            raise InvariantFailure(
                "Page dateTime smoke check changed Page content or content-object identity."
            )


async def _capture_timestamp_snapshot(
    client: MCPStdioClient,
    notebook_id: str,
) -> dict[str, Any]:
    """Capture safety evidence without treating its normalized model as timestamp proof."""

    return await capture_snapshot(client, notebook_id)


async def _read_source_date_time(
    client: MCPStdioClient,
    *,
    notebook_id: str,
    page_id: str,
    route: str,
) -> str:
    """Read the one ``dateTime`` attribute from the exact COM source for a route."""

    result = await client.call_tool(
        "read_verified_page_datetime",
        {
            "notebook_id": notebook_id,
            "page_id": page_id,
            "route": route,
        },
        retry_read=False,
    )
    if result.get("status") != "observed" or result.get("attribute_name") != "dateTime":
        raise InvariantFailure("Verified Page dateTime source field is no longer observable.")
    value = result.get("date_time")
    if not isinstance(value, str) or not value:
        raise InvariantFailure("Verified Page dateTime source read returned an invalid value.")
    return value


def _case(
    *,
    label: str,
    route: str,
    source: str,
    page_id: str,
    dispatch: dict[str, Any],
    same_source_before: str,
    same_source_readback: str,
) -> dict[str, Any]:
    comparison = compare_timestamp(PROBE_DATE_TIME, same_source_readback)
    dispatch_status = str(dispatch.get("status", "state_uncertain"))
    if dispatch_status == "dispatched" and dispatch.get("mutation_dispatched") is True:
        status = "verified" if comparison["status"] == "same_instant" else "readback_mismatch"
    elif dispatch_status == "precondition_drifted":
        status = "state_uncertain"
    else:
        status = dispatch_status
    return {
        "label": label,
        "resource_type": "page",
        "route": route,
        "object_id": page_id,
        "attribute_name": "dateTime",
        "source": source,
        "status": status,
        "mutation_dispatched": dispatch.get("mutation_dispatched") is True,
        "requested": PROBE_DATE_TIME,
        "same_source_before": same_source_before,
        "same_source_readback": same_source_readback,
        "comparison": comparison,
        "bridge_operation": dispatch.get("bridge_operation"),
        "mutation_attempts": dispatch.get("mutation_attempts", 0),
        "mutation_replayed": dispatch.get("mutation_replayed", False),
        "bridge_error_type": dispatch.get("error_type"),
        "bridge_hresult": dispatch.get("hresult"),
        "reason": dispatch.get("reason"),
    }


async def _execute_timestamp_fidelity_probe(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    notebook_id = str(manifest["notebook"]["id"])
    out = scenario_dir(options.run_dir, "prob-timestamp-fidelity")
    declared = {
        key: resolve_manifest_item(manifest, key)
        for _label, key, _route, _source in _PROBE_SPECS
    }
    async with scenario_client(
        client,
        policy=TIMESTAMP_FIDELITY_POLICY,
        allowed_tools=TIMESTAMP_FIDELITY_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as active_client:
        if not getattr(active_client.policy, "timestamp_fidelity_probe_enabled", False):
            raise RunnerFailure("Verified Page dateTime gate is not enabled.")
        before = await _capture_timestamp_snapshot(active_client, notebook_id)
        target_ids = {str(item["id"]) for item in declared.values()}
        targets: dict[str, dict[str, Any]] = {}
        for label, key, _route, _source in _PROBE_SPECS:
            current = find_snapshot_item(before, str(declared[key]["id"]))
            if current is None or current.get("resource_type") != "page":
                raise RunnerFailure(f"Verified Page dateTime {label} target is missing.")
            targets[label] = current
        current_snapshot = before
        cases: list[dict[str, Any]] = []
        for index, (label, _key, route, source) in enumerate(_PROBE_SPECS, start=1):
            page_id = str(targets[label]["id"])
            current = find_snapshot_item(current_snapshot, page_id)
            if current is None or current.get("resource_type") != "page":
                raise InvariantFailure("Verified Page dateTime target drifted before mutation.")
            expected_date_time = await _read_source_date_time(
                active_client,
                notebook_id=notebook_id,
                page_id=page_id,
                route=route,
            )
            dispatch = await active_client.call_tool(
                "set_verified_page_datetime",
                {
                    "notebook_id": notebook_id,
                    "page_id": page_id,
                    "expected_parent_id": current.get("parent_id"),
                    "expected_hierarchy_modified": current.get("modified"),
                    "expected_date_time": expected_date_time,
                    "route": route,
                    "date_time": PROBE_DATE_TIME,
                },
                retry_read=False,
            )
            after_snapshot = await _capture_timestamp_snapshot(active_client, notebook_id)
            same_source_readback = await _read_source_date_time(
                active_client,
                notebook_id=notebook_id,
                page_id=page_id,
                route=route,
            )
            _assert_safety(current_snapshot, after_snapshot)
            case = _case(
                label=label,
                route=route,
                source=source,
                page_id=page_id,
                dispatch=dispatch,
                same_source_before=expected_date_time,
                same_source_readback=same_source_readback,
            )
            cases.append(case)
            write_json(out / f"timestamp-write-{index:02d}.json", case)
            current_snapshot = after_snapshot

        final_first = await _capture_timestamp_snapshot(active_client, notebook_id)
        final_first_values = {
            str(case["label"]): await _read_source_date_time(
                active_client,
                notebook_id=notebook_id,
                page_id=str(case["object_id"]),
                route=str(case["route"]),
            )
            for case in cases
        }
        final_second = await _capture_timestamp_snapshot(active_client, notebook_id)
        final_second_values = {
            str(case["label"]): await _read_source_date_time(
                active_client,
                notebook_id=notebook_id,
                page_id=str(case["object_id"]),
                route=str(case["route"]),
            )
            for case in cases
        }
        _assert_safety(current_snapshot, final_first)
        _assert_safety(final_first, final_second)
        for index, case in enumerate(cases, start=1):
            label = str(case["label"])
            first = final_first_values[label]
            second = final_second_values[label]
            stable = (
                first == second
                and compare_timestamp(PROBE_DATE_TIME, second)["status"] == "same_instant"
            )
            case["stable_readback"] = stable
            if case["status"] == "verified" and not stable:
                case["status"] = "unstable_same_source_readback"
            write_json(out / f"timestamp-write-{index:02d}.json", case)

        all_routes_verified = all(case["status"] == "verified" for case in cases)
        matrix = {
            "schema_version": 3,
            "scenario": "prob-timestamp-fidelity",
            "content_exposed": False,
            "verified_capability": {
                "resource_type": "page",
                "attribute_name": "dateTime",
                "routes": ["update_hierarchy", "update_page_content"],
                "supported_precision": "whole_seconds",
            },
            "requested_date_time": PROBE_DATE_TIME,
            "assessment_complete": True,
            "all_routes_verified": all_routes_verified,
            "cases": cases,
        }
        write_json(
            out / "timestamp-final-stability.json",
            {
                "schema_version": 3,
                "content_exposed": False,
                "cases": [
                    {
                        "label": case["label"],
                        "source": case["source"],
                        "object_id": case["object_id"],
                        "first": final_first_values[str(case["label"])],
                        "second": final_second_values[str(case["label"])],
                        "stable": case["stable_readback"],
                    }
                    for case in cases
                ],
            },
        )
        write_json(out / "timestamp-capability-matrix.json", matrix)
        worksite = {
            "status": "timestamp_smoke_worksite_preserved",
            "target_ids": sorted(target_ids),
            "manual_cleanup_required": True,
            "reason": "Verified Page dateTime smoke check uses only disposable run-scoped targets.",
        }
        if keep_worksite:
            write_json(out / "worksite.json", worksite)
        result = {
            "scenario": "prob-timestamp-fidelity",
            "status": "passed" if all_routes_verified else "failed",
            "target_ids": sorted(target_ids),
            "restored": False,
            "worksite_preserved": keep_worksite,
            "remaining_state": worksite if keep_worksite else None,
            "matrix": matrix,
        }
        write_json(out / "result.json", result)
        if not all_routes_verified:
            raise InvariantFailure(
                "Verified Page dateTime smoke check contains a failed or unstable route."
            )
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class TimestampFidelityProbeScenario(Scenario):
    name = "prob-timestamp-fidelity"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: verify the proven seconds-precision Page dateTime write/read-back "
        "contract through UpdateHierarchy and UpdatePageContent."
    )
    included_in_all = False
    worksite_dry_run_action = "preserve-timestamp-smoke-worksite"

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
        return await _execute_timestamp_fidelity_probe(args, options, manifest, client=client)


__all__ = ["TimestampFidelityProbeScenario"]
