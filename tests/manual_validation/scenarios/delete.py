"""Manifest-allowlisted non-permanent delete scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import DELETE_POLICY, MCPStdioClient, scenario_client
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
    assert_restored,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    flatten_tree,
    is_descendant_of,
    resolve_manifest_item,
    scenario_dir,
    stable_item,
    utc_now,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .common.expected_rejection import expect_mutation_preflight_rejection
from .fixture_recipes.delete import RECIPE
from .common.config import DELETE_TOOLS
from .common.report import render_report


async def _execute_delete(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    delete_sandbox = resolve_manifest_item(manifest, "delete_sandbox")
    target_keys = tuple(key for key in (
        "disposable_page_target",
        "disposable_page_target_second",
        "disposable_section_target",
        "disposable_group",
    ) if key in manifest.get("structure", {}))
    use_batch = len(target_keys) > 1
    declared_targets = [resolve_manifest_item(manifest, key) for key in target_keys]
    out = scenario_dir(options.run_dir, "delete")
    async with scenario_client(
        client,
        policy=DELETE_POLICY,
        allowed_tools=DELETE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        budget_rejection: dict[str, Any] | None = None
        if "budget_section" in manifest.get("structure", {}):
            if sum(
                item.get("resource_type") == "page"
                for item in before.get("items", [])
            ) <= 3:
                raise InvariantFailure(
                    "Delete fixture does not exceed its test Batch effective Page limit."
                )
            budget_section_declared = resolve_manifest_item(manifest, "budget_section")
            budget_section = find_snapshot_item(before, budget_section_declared["id"])
            if budget_section is None:
                raise RunnerFailure("Batch budget rejection Section is not active.")
            budget_rejection = await expect_mutation_preflight_rejection(
                client,
                "delete_section",
                {
                    "items": [
                        {
                            "section_id": budget_section["id"],
                            "expected_name": display_name(budget_section),
                            "expected_parent_id": budget_section["parent_id"],
                            "expected_modified": budget_section.get("modified"),
                        }
                    ]
                },
                out / "expected-effective-page-budget-rejection.json",
                label="delete-section-effective-page-budget",
                expected_message_fragment="effective Page budget",
            )
            rejection_after = await capture_snapshot(client, notebook_id)
            write_json(out / "expected-budget-rejection-after.json", rejection_after)
            assert_restored(before, rejection_after)
        currents = []
        deleted_results = []
        batches: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for declared in declared_targets:
            current = find_snapshot_item(before, declared["id"])
            if current is None:
                raise RunnerFailure("Batch Delete target is not active in the current notebook snapshot.")
            if not is_descendant_of(before, current["id"], delete_sandbox["id"]):
                raise RunnerFailure("Batch Delete target escaped the manifest Delete-Sandbox.")
            resource_type = current.get("resource_type")
            if resource_type == "page":
                tool = "delete_page"
                batch_item = {
                    "page_id": current["id"],
                    "expected_title": display_name(current),
                    "expected_section_id": current["section_id"],
                    "expected_modified": current.get("modified"),
                }
            elif resource_type in {"section", "section_group"}:
                tool = f"delete_{resource_type}"
                batch_item = {
                    ("section_id" if resource_type == "section" else "section_group_id"): current["id"],
                    "expected_name": display_name(current),
                    "expected_parent_id": current["parent_id"],
                    "expected_modified": current.get("modified"),
                }
            else:
                raise RunnerFailure("Batch Delete supports only Page/Section/SectionGroup targets.")
            currents.append(current)
            batches.setdefault(tool, []).append((current, batch_item))
        for tool, entries in batches.items():
            if use_batch:
                response = await client.call_tool(
                    tool, {"items": [batch_item for _current, batch_item in entries]}
                )
                if response.get("applied_count") != len(entries):
                    raise InvariantFailure(
                        "Batch Delete did not apply every grouped input item."
                    )
                if response.get("final_hierarchy", {}).get("item_count", 0) < len(
                    entries
                ):
                    raise InvariantFailure(
                        "Batch Delete omitted its complete final hierarchy read-back."
                    )
                deleted_items = [
                    outcome.get("result", {})
                    for outcome in response.get("items", [])
                ]
            else:
                current, batch_item = entries[0]
                response = await client.call_tool(
                    tool,
                    {
                        **batch_item,
                        **({"permanently": False} if current["resource_type"] != "page" else {}),
                    },
                )
                deleted_items = [response]
            if any(deleted.get("permanently") is not False for deleted in deleted_items):
                raise InvariantFailure(
                    "Batch Delete item did not explicitly confirm permanently=false."
                )
            deleted_results.append(response)
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        if any(find_snapshot_item(after, current["id"]) is not None for current in currents):
            raise InvariantFailure("A Batch Delete target is still visible in the active snapshot.")
        recycle_tree_result = await client.call_tool(
            "expand_hierarchy",
            {"root_id": notebook_id, "max_depth": 8, "include_recycle_bin": True},
        )
        recycle_items = [stable_item(item) for item in flatten_tree(recycle_tree_result["tree"])]
        recycle_snapshot = {
            "captured_at": utc_now(),
            "notebook_id": notebook_id,
            "include_recycle_bin": True,
            "items": recycle_items,
        }
        write_json(out / "recycle-bin.json", recycle_snapshot)
        recycled_items = {
            current["id"]: next((item for item in recycle_items if item.get("id") == current["id"]), None)
            for current in currents
        }
        if any(value is not None and value.get("is_in_recycle_bin") is not True for value in recycled_items.values()):
            raise InvariantFailure("A Batch Delete target remains visible without a recycle-bin marker.")
        restoration = {
            "status": "not_attempted",
            "reason": (
                "The typed MCP profile has no recycle-bin restore tool. "
                "A later scenario command creates its own fresh disposable fixture."
            ),
            "target_ids": [current["id"] for current in currents],
        }
        write_json(out / "restored.json", restoration)
        keep_worksite = bool(getattr(args, "keep_worksite", False))
        worksite = {
            "status": (
                "batch_targets_in_recycle_bin_or_absent"
            ),
            "target_ids": [current["id"] for current in currents],
            "permanently": False,
            "recycle_bin_verified_ids": [object_id for object_id, value in recycled_items.items() if value is not None],
            "manual_cleanup_required": True,
            "cleanup": (
                "Restore or remove the exact disposable batch targets from the OneNote recycle bin after inspection."
            ),
        }
        if keep_worksite:
            write_json(out / "worksite.json", worksite)
        result = {
            "scenario": "delete",
            "status": "passed",
            "target_ids": [current["id"] for current in currents],
            "target_keys": list(target_keys),
            "batch_results": deleted_results,
            "budget_rejection": budget_rejection,
            "large_notebook_small_page_batch": {
                "notebook_pages_exceed_effective_limit": budget_rejection is not None,
                "leaf_page_targets": sum(
                    current.get("resource_type") == "page" for current in currents
                ),
                "applied": use_batch,
            },
            "permanently": False,
            "restored": False,
            "worksite_preserved": keep_worksite,
            "remaining_state": (
                worksite
                if keep_worksite
                else "This run's disposable group remains in the recycle bin."
            ),
        }
        if not use_batch:
            result["target_id"] = currents[0]["id"]
            result["target_key"] = target_keys[0]
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class DeleteScenario(Scenario):
    name = "delete"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: non-permanently delete disposable Page/Section/SectionGroup items, "
        "report, then close or keep."
    )
    included_in_all = True
    worksite_dry_run_action = "preserve-recycle-bin-state"

    def prepare_arguments(
        self,
        args: argparse.Namespace,
        manifest: dict[str, Any],
    ) -> None:
        args.delete_target_id = resolve_manifest_item(manifest, "disposable_group")["id"]

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_delete(args, options, manifest, client=client)
