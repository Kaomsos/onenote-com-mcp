"""Rename-and-restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import ClientFailure, MCPStdioClient, WRITE_POLICY, scenario_client
from ..runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
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
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .common.config import RENAME_TOOLS
from .common.report import render_report


async def _execute_rename(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    target_key = args.target
    target = resolve_manifest_item(manifest, target_key)
    resource_type = target.get("resource_type")
    if resource_type not in {"section", "section_group"}:
        raise RunnerFailure("Rename target must be a section or section group.")
    tool = "rename_section" if resource_type == "section" else "rename_section_group"
    id_key = "section_id" if resource_type == "section" else "section_group_id"
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    out = scenario_dir(options.run_dir, "rename")
    async with scenario_client(
        client,
        policy=WRITE_POLICY,
        allowed_tools=RENAME_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, target["id"])
        if current is None:
            raise RunnerFailure("Rename target is not active in the current notebook snapshot.")
        original_name = display_name(current)
        new_name = args.new_name or f"{original_name}-Smoke-Renamed"
        if new_name == original_name:
            raise RunnerFailure("--new-name must differ from the current name.")
        forward = await client.call_tool(
            tool,
            {
                id_key: current["id"],
                "new_name": new_name,
                "expected_name": original_name,
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
            },
        )
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        changed = find_snapshot_item(after, current["id"])
        validation_error: InvariantFailure | None = None
        try:
            if changed is None or display_name(changed) != new_name or changed.get("parent_id") != current.get("parent_id"):
                raise InvariantFailure("Rename read-back did not preserve ID/parent and apply the requested name.")
            if snapshot_ids(before) != snapshot_ids(after):
                raise InvariantFailure("Rename changed one or more hierarchy object IDs.")
            if page_topology(before) != page_topology(after):
                raise InvariantFailure("Rename changed Page IDs, order, level, or parent relationships.")
            if before["page_hashes"] != after["page_hashes"]:
                raise InvariantFailure("Rename changed one or more Page XML hashes.")
        except InvariantFailure as exc:
            validation_error = exc
        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_rename",
                "target_ids": [current["id"]],
                "target_id": current["id"],
                "original_name": original_name,
                "current_name": display_name(changed) if changed is not None else new_name,
                "verified": validation_error is None,
                "manual_cleanup_required": True,
                "cleanup": (
                    f"Rename {resource_type} {current['id']} back to {original_name!r} "
                    "after inspection."
                ),
            }
            write_json(out / "worksite.json", worksite)
            if validation_error is not None:
                raise validation_error
            result = {
                "scenario": "rename",
                "status": "passed",
                "target_id": current["id"],
                "original_name": original_name,
                "temporary_name": new_name,
                "forward_result": forward.get("item"),
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result
        restore_target = changed or forward.get("item")
        if not isinstance(restore_target, dict):
            raise RestoreFailure("Rename succeeded but no target identity was available for restoration.")
        try:
            await client.call_tool(
                tool,
                {
                    id_key: restore_target["id"],
                    "new_name": original_name,
                    "expected_name": new_name,
                    "expected_parent_id": current["parent_id"],
                    "expected_modified": restore_target.get("modified"),
                },
            )
            restored = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Rename succeeded but restoration failed: {exc}") from exc
        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "rename",
            "status": "passed",
            "target_id": current["id"],
            "original_name": original_name,
            "temporary_name": new_name,
            "forward_result": forward.get("item"),
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class RenameScenario(Scenario):
    name = "rename"
    help_text = "GATED: create, rename/restore or preserve, report, then close or keep."
    registered_for_all = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--target",
            choices=["group_a", "group_b", "content_section"],
            default="content_section",
        )
        parser.add_argument("--new-name")

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_rename(args, options, manifest, client=client)
