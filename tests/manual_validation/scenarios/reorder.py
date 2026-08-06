"""Page reorder-and-restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import ClientFailure, MCPStdioClient, WRITE_POLICY, scenario_client
from ..runner import (
    InvariantFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
    assert_restored,
    assert_valid_page_tree,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)
from ._config import REORDER_TOOLS
from .report import render_report


def page_predecessor(pages: list[dict[str, Any]], page_id: str) -> str:
    index = next((index for index, page in enumerate(pages) if page.get("id") == page_id), -1)
    if index < 0:
        raise RunnerFailure(f"Page is missing from snapshot: {page_id}")
    return "" if index == 0 else str(pages[index - 1]["id"])


async def run_reorder(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    target = resolve_manifest_item(manifest, "sibling_page")
    after_target = resolve_manifest_item(manifest, "parent_page")
    section = resolve_manifest_item(manifest, "move_source")
    out = scenario_dir(options.run_dir, "reorder")
    async with scenario_client(
        client,
        policy=WRITE_POLICY,
        allowed_tools=REORDER_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        original = find_snapshot_item(before, target["id"])
        if original is None:
            raise RunnerFailure("Reorder target is not active.")
        pages = sorted(
            [item for item in before["items"] if item.get("section_id") == section["id"]],
            key=lambda item: int(item["order"]),
        )
        original_after = page_predecessor(pages, original["id"])
        original_level = int(original["page_level"])
        forward = await client.call_tool(
            "reorder_page",
            {
                "page_id": original["id"],
                "expected_title": display_name(original),
                "expected_section_id": section["id"],
                "after_page_id": after_target["id"],
                "page_level": args.page_level,
                "expected_modified": original.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        changed = find_snapshot_item(after, original["id"])
        after_pages = sorted(
            [item for item in after["items"] if item.get("section_id") == section["id"]],
            key=lambda item: int(item["order"]),
        )
        validation_error: InvariantFailure | None = None
        try:
            if changed is None or page_predecessor(after_pages, original["id"]) != after_target["id"]:
                raise InvariantFailure("Reorder read-back position does not match the requested predecessor.")
            if int(changed["page_level"]) != args.page_level:
                raise InvariantFailure("Reorder read-back page_level does not match the requested level.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Reorder changed one or more hierarchy object IDs.")
            assert_valid_page_tree(after, section["id"])
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Reorder changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        restore_target = changed or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Reorder succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                "reorder_page",
                {
                    "page_id": restore_target["id"],
                    "expected_title": display_name(original),
                    "expected_section_id": section["id"],
                    "after_page_id": original_after,
                    "page_level": original_level,
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Reorder succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "reorder",
            "status": "passed",
            "target_id": original["id"],
            "temporary_after_page_id": after_target["id"],
            "temporary_page_level": args.page_level,
            "restored": True,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result
