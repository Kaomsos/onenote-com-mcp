"""Section move-and-restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import ClientFailure, MCPStdioClient, MOVE_POLICY
from ..runner import (
    InvariantFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
    assert_restored,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    page_topology,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)
from ._config import MOVE_TOOLS
from .report import render_report


async def run_move(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    target = resolve_manifest_item(manifest, "move_source")
    source = resolve_manifest_item(manifest, "group_a")
    destination = resolve_manifest_item(manifest, "group_b")
    out = scenario_dir(options.run_dir, "move")
    async with MCPStdioClient(
        policy=MOVE_POLICY,
        allowed_tools=MOVE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, target["id"])
        if current is None or current.get("parent_id") != source["id"]:
            raise RunnerFailure("Move-Source is not currently under Group-A; refusing to guess recovery state.")
        forward = await client.call_tool(
            "move_section",
            {
                "section_id": current["id"],
                "destination_parent_id": destination["id"],
                "expected_name": display_name(current),
                "expected_parent_id": source["id"],
                "expected_modified": current.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        moved = find_snapshot_item(after, current["id"])
        validation_error: InvariantFailure | None = None
        try:
            if moved is None or moved.get("parent_id") != destination["id"]:
                raise InvariantFailure("Move read-back did not preserve ID and apply the destination parent.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Move changed one or more hierarchy object IDs.")
            if page_topology(before, current["id"]) != page_topology(after, current["id"]):
                raise InvariantFailure("Move changed Page IDs, order, level, or parent relationships.")
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Move changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        restore_target = moved or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Move succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                "move_section",
                {
                    "section_id": restore_target["id"],
                    "destination_parent_id": source["id"],
                    "expected_name": display_name(current),
                    "expected_parent_id": destination["id"],
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Move succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "move",
            "status": "passed",
            "target_id": current["id"],
            "destination_parent_id": destination["id"],
            "restored": True,
            "warning": "This validates one installed OneNote/Office combination, not universal COM behavior.",
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result
