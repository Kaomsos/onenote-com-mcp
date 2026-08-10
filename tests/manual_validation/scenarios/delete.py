"""Manifest-allowlisted non-permanent delete scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import DELETE_POLICY, MCPStdioClient, scenario_client
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
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
    target_key = "disposable_group"
    target = resolve_manifest_item(manifest, target_key)
    allowed = {target["id"]: target_key}
    if args.delete_target_id not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise RunnerFailure(f"Delete target is not manifest-allowlisted. Allowed IDs: {allowed_text}")
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
        current = find_snapshot_item(before, args.delete_target_id)
        if current is None:
            raise RunnerFailure("Delete target is not active in the current notebook snapshot.")
        if not is_descendant_of(before, current["id"], delete_sandbox["id"]):
            raise RunnerFailure("Delete target is no longer a descendant of the manifest Delete-Sandbox.")
        resource_type = current.get("resource_type")
        if resource_type == "page":
            tool = "delete_page"
            arguments = {
                "page_id": current["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        elif resource_type in {"section", "section_group"}:
            tool = "delete_section" if resource_type == "section" else "delete_section_group"
            id_key = "section_id" if resource_type == "section" else "section_group_id"
            arguments = {
                id_key: current["id"],
                "expected_name": display_name(current),
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        else:
            raise RunnerFailure("Delete smoke supports only allowlisted Page/Section/SectionGroup targets.")
        deleted = await client.call_tool(tool, arguments)
        if deleted.get("permanently") is not False:
            raise InvariantFailure("Delete response did not explicitly confirm permanently=false.")
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        if find_snapshot_item(after, current["id"]) is not None:
            raise InvariantFailure("Deleted target is still visible in the default active snapshot.")
        recycle_tree_result = await client.call_tool(
            "get_tree",
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
        recycled = next((item for item in recycle_items if item.get("id") == current["id"]), None)
        if recycled is not None and recycled.get("is_in_recycle_bin") is not True:
            raise InvariantFailure("Delete target remains visible without an is_in_recycle_bin marker.")
        restoration = {
            "status": "not_attempted",
            "reason": (
                "The typed MCP profile has no recycle-bin restore tool. "
                "A later scenario command creates its own fresh disposable fixture."
            ),
            "target_id": current["id"],
        }
        write_json(out / "restored.json", restoration)
        keep_worksite = bool(getattr(args, "keep_worksite", False))
        worksite = {
            "status": (
                "deleted_target_in_recycle_bin"
                if recycled is not None
                else "deleted_target_absent_from_active_tree"
            ),
            "target_ids": [current["id"]],
            "target_id": current["id"],
            "permanently": False,
            "recycle_bin_verified": recycled is not None,
            "manual_cleanup_required": True,
            "cleanup": (
                f"Restore or remove disposable target {current['id']} from the OneNote "
                "recycle bin after inspection."
            ),
        }
        if keep_worksite:
            write_json(out / "worksite.json", worksite)
        result = {
            "scenario": "delete",
            "status": "passed",
            "target_id": current["id"],
            "target_key": allowed[current["id"]],
            "permanently": False,
            "restored": False,
            "worksite_preserved": keep_worksite,
            "remaining_state": (
                worksite
                if keep_worksite
                else "This run's disposable group remains in the recycle bin."
            ),
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class DeleteScenario(Scenario):
    name = "delete"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: create, non-permanently delete the disposable group, report, then close or keep."
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
