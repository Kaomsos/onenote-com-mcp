"""Verified copy plus non-permanent source deletion Page move scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, RECONSTRUCTIVE_MOVE_PAGE_POLICY
from ..runner import (
    InvariantFailure,
    RunnerFailure,
    RuntimeOptions,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    flatten_tree,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    stable_item,
    timestamp,
    utc_now,
    validate_manifest_notebook,
    write_json,
)
from .copy import call_with_result_evidence
from ._config import RECONSTRUCTIVE_MOVE_PAGE_TOOLS
from .copy_invariants import assert_copy_mapping, expected_copy_source_items
from .report import render_report


async def run_reconstructive_move_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    source = resolve_manifest_item(manifest, "disposable_page")
    destination = resolve_manifest_item(manifest, "move_source")
    destination_title = f"Moved-Disposable-{timestamp()}"
    out = scenario_dir(options.run_dir, "reconstructive-move-page")
    async with MCPStdioClient(
        policy=RECONSTRUCTIVE_MOVE_PAGE_POLICY,
        allowed_tools=RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
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
        recycle_tree = await client.call_tool(
            "get_tree",
            {"root_id": notebook_id, "max_depth": 8, "include_recycle_bin": True},
        )
        recycle_items = [stable_item(item) for item in flatten_tree(recycle_tree["tree"])]
        recycled_source_ids = {
            item["id"]
            for item in recycle_items
            if item.get("id") in source_subtree_ids
            and item.get("is_in_recycle_bin") is True
        }
        if recycled_source_ids != source_subtree_ids:
            raise InvariantFailure(
                "Reconstructive Move source subtree could not be proven to be in the OneNote recycle bin."
            )
        write_json(
            out / "recycle-bin.json",
            {
                "captured_at": utc_now(),
                "notebook_id": notebook_id,
                "include_recycle_bin": True,
                "items": recycle_items,
            },
        )
        remaining = {
            "status": "source_subtree_in_recycle_bin",
            "source_id": current["id"],
            "source_ids": sorted(source_subtree_ids),
            "target_id": target_id,
            "reason": "Typed recycle-bin restore is unavailable; run create to replenish the fixture.",
        }
        write_json(out / "restored.json", remaining)
        result = {
            "scenario": "reconstructive-move-page",
            "status": "passed",
            "target_id": current["id"],
            "new_target_id": target_id,
            "restored": False,
            "remaining_state": remaining,
            "copy_report": moved["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result
