"""Verified copy plus non-permanent source deletion Page move scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import (
    MCPStdioClient,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    scenario_client,
)
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    timestamp,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .common.config import RECONSTRUCTIVE_MOVE_PAGE_TOOLS
from .common.copy_invariants import (
    assert_copy_fixture_capabilities,
    assert_copy_mapping,
    expected_copy_source_items,
)
from .common.copy_runtime import call_with_result_evidence
from .common.report import render_report


async def _execute_reconstructive_move_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    source = resolve_manifest_item(manifest, "disposable_page")
    destination = resolve_manifest_item(manifest, "move_source")
    destination_title = f"Moved-Disposable-{timestamp()}"
    out = scenario_dir(options.run_dir, "reconstructive-move-page")
    async with scenario_client(
        client,
        policy=RECONSTRUCTIVE_MOVE_PAGE_POLICY,
        allowed_tools=RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, source["id"])
        if current is None:
            raise RunnerFailure("Disposable Page is not active; run create to replenish the fixture.")
        planned = await client.call_tool(
            "plan_reconstructive_move_page",
            {
                "page_id": current["id"],
                "destination_section_id": destination["id"],
                "destination_title": destination_title,
            },
        )
        write_json(out / "plan.json", planned)
        assert_copy_fixture_capabilities(planned)
        moved = await call_with_result_evidence(
            client,
            "reconstructive_move_page",
            {
                "page_id": current["id"],
                "destination_section_id": destination["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "destination_title": destination_title,
                "plan_digest": planned["plan_digest"],
            },
            out / "copy-result.json",
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        source_subtree_ids = {
            item["id"] for item in expected_copy_source_items(before, current["id"])
        }
        remaining_source_ids = source_subtree_ids & snapshot_ids(after)
        if remaining_source_ids:
            raise InvariantFailure(
                f"Reconstructive Move source subtree remains active: {sorted(remaining_source_ids)}"
            )
        target_id = moved.get("item", {}).get("id")
        if not target_id or find_snapshot_item(after, target_id) is None:
            raise InvariantFailure("Reconstructive Move target is missing from the active snapshot.")
        assert_copy_mapping(
            before,
            after,
            current["id"],
            destination["id"],
            destination_title,
            moved,
        )
        remaining = {
            "status": "source_subtree_removed_nonpermanently",
            "source_id": current["id"],
            "source_ids": sorted(source_subtree_ids),
            "target_id": target_id,
            "target_ids": [target_id, *sorted(source_subtree_ids)],
            "recycle_bin_verification": moved.get(
                "recycle_bin_verification", "not_reported"
            ),
            "recycled_source_ids": moved.get("recycled_source_ids", []),
            "recycle_unverified_source_ids": moved.get(
                "recycle_unverified_source_ids", []
            ),
            "manual_cleanup_required": True,
            "cleanup": (
                "Inspect the active copied target. In OneNote UI, restore or remove any "
                "disposable source Pages shown in Deleted Notes, then remove the copied target."
            ),
            "reason": (
                "The source subtree is absent from the active hierarchy after non-permanent "
                "DeleteHierarchy; recycle-bin visibility is not an acceptance requirement."
            ),
        }
        write_json(out / "restored.json", remaining)
        keep_worksite = bool(getattr(args, "keep_worksite", False))
        if keep_worksite:
            write_json(out / "worksite.json", remaining)
        result = {
            "scenario": "reconstructive-move-page",
            "status": "passed",
            "target_id": current["id"],
            "new_target_id": target_id,
            "restored": False,
            "worksite_preserved": keep_worksite,
            "source_deleted_nonpermanently": True,
            "recycle_bin_verification": remaining["recycle_bin_verification"],
            "remaining_state": remaining,
            "copy_report": moved["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class ReconstructiveMovePageScenario(Scenario):
    name = "reconstructive-move-page"
    help_text = (
        "GATED: create, strictly move the disposable Page by verified Copy plus "
        "non-permanent source removal, report, then close or keep."
    )
    timeout_default = 1_800
    registered_for_all = True

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_reconstructive_move_page(args, options, manifest, client=client)
