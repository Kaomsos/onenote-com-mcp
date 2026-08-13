"""Shared evidence and restore flow for typed container reorder scenarios."""

from __future__ import annotations

import time
from typing import Any

from ...mcp_stdio_client import ClientFailure, MCPStdioClient, ScenarioPolicy, scenario_client
from ...runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions
from ...test_utils import (
    assert_restored,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)
from .report import render_report


def direct_order(snapshot: dict[str, Any], parent_id: str, resource_type: str) -> list[str]:
    return [
        str(item["id"])
        for item in snapshot.get("items", [])
        if item.get("resource_type") == resource_type and item.get("parent_id") == parent_id
    ]


def predecessor(snapshot: dict[str, Any], object_id: str, resource_type: str) -> str:
    target = find_snapshot_item(snapshot, object_id)
    if target is None:
        raise RunnerFailure(f"Reorder target is missing from snapshot: {object_id}")
    order = direct_order(snapshot, str(target["parent_id"]), resource_type)
    try:
        index = order.index(object_id)
    except ValueError as exc:
        raise RunnerFailure(f"Reorder target is missing from its direct sibling sequence: {object_id}") from exc
    return "" if index == 0 else order[index - 1]


def reordered_ids(order: list[str], target_id: str, after_id: str) -> list[str]:
    remaining = [object_id for object_id in order if object_id != target_id]
    insertion_index = 0 if not after_id else remaining.index(after_id) + 1
    remaining.insert(insertion_index, target_id)
    return remaining


def relationship_signature(snapshot: dict[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            item.get("id"),
            item.get("resource_type"),
            item.get("parent_id"),
            item.get("notebook_id"),
            item.get("section_id"),
            item.get("order"),
            item.get("page_level"),
            item.get("parent_page_id"),
            display_name(item),
        )
        for item in snapshot.get("items", [])
    )


def assert_preserved(before: dict[str, Any], after: dict[str, Any]) -> None:
    if snapshot_ids(before) != snapshot_ids(after):
        raise InvariantFailure("Container reorder changed one or more hierarchy object IDs.")
    if relationship_signature(before) != relationship_signature(after):
        raise InvariantFailure("Container reorder changed an object relationship or Page order.")
    if before.get("page_hashes") != after.get("page_hashes"):
        raise InvariantFailure("Container reorder changed one or more Page content hashes.")
    if before.get("page_objects") != after.get("page_objects"):
        raise InvariantFailure("Container reorder changed one or more Page object projections.")


async def execute_container_reorder(
    *,
    args: Any,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    scenario_name: str,
    resource_type: str,
    tool_name: str,
    id_parameter: str,
    after_parameter: str,
    plans: tuple[tuple[str, str], ...],
    policy: ScenarioPolicy,
    allowed_tools: set[str],
    client: MCPStdioClient | None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    out = scenario_dir(options.run_dir, scenario_name)
    async with scenario_client(
        client,
        policy=policy,
        allowed_tools=allowed_tools,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as active_client:
        before = await capture_snapshot(active_client, notebook_id)
        write_json(out / "before.json", before)
        original: list[dict[str, str]] = []
        before_orders: dict[str, list[str]] = {}
        expected_orders: dict[str, list[str]] = {}
        current = before
        for index, (target_key, after_key) in enumerate(plans, start=1):
            case_started = time.monotonic()
            options.progress.unit_started("case", f"reorder-{index}", index, len(plans))
            declared_target = resolve_manifest_item(manifest, target_key)
            declared_after = resolve_manifest_item(manifest, after_key)
            target = find_snapshot_item(current, str(declared_target["id"]))
            if target is None:
                raise RunnerFailure(f"Manifest reorder target is not active: {target_key}")
            parent_id = str(target["parent_id"])
            before_orders.setdefault(parent_id, direct_order(before, parent_id, resource_type))
            current_order = expected_orders.get(
                parent_id,
                direct_order(current, parent_id, resource_type),
            )
            expected_orders[parent_id] = reordered_ids(
                current_order,
                str(target["id"]),
                str(declared_after["id"]),
            )
            original.append(
                {
                    "target_key": target_key,
                    "target_id": str(target["id"]),
                    "parent_id": str(target["parent_id"]),
                    "original_after_id": predecessor(
                        before, str(target["id"]), resource_type
                    ),
                    "temporary_after_id": str(declared_after["id"]),
                }
            )
            await active_client.call_tool(
                tool_name,
                {
                    id_parameter: target["id"],
                    "expected_name": display_name(target),
                    "expected_parent_id": target["parent_id"],
                    after_parameter: declared_after["id"],
                    "expected_modified": target.get("modified"),
                },
            )
            current = await capture_snapshot(active_client, notebook_id)
            write_json(out / f"forward-{index}.json", current)
            options.progress.unit_completed(
                "case",
                f"reorder-{index}",
                index,
                len(plans),
                elapsed_seconds=time.monotonic() - case_started,
            )

        after = current
        write_json(out / "after.json", after)
        validation_error: InvariantFailure | None = None
        try:
            assert_preserved(before, after)
            for plan in original:
                if predecessor(after, plan["target_id"], resource_type) != plan["temporary_after_id"]:
                    raise InvariantFailure(
                        "Container reorder read-back does not match the requested predecessor."
                    )
            for parent_id, expected_order in expected_orders.items():
                if direct_order(after, parent_id, resource_type) != expected_order:
                    raise InvariantFailure(
                        "Container reorder changed the order of an unrequested same-type sibling."
                    )
        except InvariantFailure as exc:
            validation_error = exc

        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": f"preserved_after_{scenario_name}",
                "target_ids": [plan["target_id"] for plan in original],
                "operations": original,
                "original_sibling_orders": before_orders,
                "current_sibling_orders": expected_orders,
                "verified": validation_error is None,
                "manual_cleanup_required": True,
                "cleanup": "Restore each target to original_after_id using its exact current ID and confirmation fields.",
            }
            write_json(out / "worksite.json", worksite)
            if validation_error is not None:
                raise validation_error
            result = {
                "scenario": scenario_name,
                "status": "passed",
                "target_ids": worksite["target_ids"],
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        try:
            restore_started = time.monotonic()
            options.progress.unit_started("restore", "container-order")
            for index, plan in enumerate(reversed(original), start=1):
                target = find_snapshot_item(current, plan["target_id"])
                if target is None:
                    raise RestoreFailure("Reorder target disappeared before restoration.")
                await active_client.call_tool(
                    tool_name,
                    {
                        id_parameter: target["id"],
                        "expected_name": display_name(target),
                        "expected_parent_id": target["parent_id"],
                        after_parameter: plan["original_after_id"],
                        "expected_modified": target.get("modified"),
                    },
                )
                current = await capture_snapshot(active_client, notebook_id)
                write_json(out / f"restore-{index}.json", current)
            restored = current
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
            for plan in original:
                if predecessor(restored, plan["target_id"], resource_type) != plan["original_after_id"]:
                    raise RestoreFailure("Restored container order does not match the before snapshot.")
            for parent_id, original_order in before_orders.items():
                if direct_order(restored, parent_id, resource_type) != original_order:
                    raise RestoreFailure("Restored full sibling order does not match the before snapshot.")
            options.progress.unit_completed(
                "restore",
                "container-order",
                elapsed_seconds=time.monotonic() - restore_started,
            )
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Container reorder succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": scenario_name,
            "status": "passed",
            "target_ids": [plan["target_id"] for plan in original],
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


__all__ = [
    "assert_preserved",
    "direct_order",
    "execute_container_reorder",
    "predecessor",
    "reordered_ids",
]
