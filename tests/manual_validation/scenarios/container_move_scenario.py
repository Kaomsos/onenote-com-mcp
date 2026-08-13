"""Shared runner for the two independently registered container Move scenarios."""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, MOVE_CONTAINERS_POLICY, scenario_client
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..run_identity import run_safe_timestamp
from ..test_utils import (
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.copy_invariants import assert_copy_mapping, expected_copy_source_items
from .common.copy_runtime import call_with_result_evidence
from .common.destination_position import assert_destination_position
from .common.report import render_report


class ContainerMoveScenario(Scenario):
    """A strict cross-Notebook Copy→one non-permanent root Delete validation."""

    resource_type = ""
    plan_tool = ""
    move_tool = ""
    tool_allowlist: set[str]
    timeout_default = 1_800
    included_in_all = False
    worksite_dry_run_action = "preserve-cross-notebook-container-move-target"

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
        validate_manifest_notebook(manifest, args.notebook_name)
        notebooks = manifest.get("notebooks")
        if not isinstance(notebooks, dict) or set(notebooks) != {"destination", "source"}:
            raise RunnerFailure("Container Move requires exact source/destination Notebook roles.")
        contract = self.spec.execution_contract
        source = resolve_manifest_item(manifest, str(contract["source_key"]))
        destination_notebook_id = str(notebooks["destination"]["id"])
        source_notebook_id = str(notebooks["source"]["id"])
        if source_notebook_id == destination_notebook_id:
            raise RunnerFailure("Container Move fixture roles resolved to the same Notebook ID.")

        async def capture_bundle(active_client: MCPStdioClient) -> dict[str, Any]:
            roles = {
                role: await capture_snapshot(active_client, str(notebooks[role]["id"]))
                for role in ("source", "destination")
            }
            merged: dict[str, Any] = {
                "notebook_id": source_notebook_id,
                "notebook_ids": {
                    role: str(notebooks[role]["id"]) for role in roles
                },
                "roles": roles,
                "items": [],
                "page_hashes": {},
                "page_objects": {},
            }
            for role in ("source", "destination"):
                merged["items"].extend(roles[role].get("items", []))
                merged["page_hashes"].update(roles[role].get("page_hashes", {}))
                merged["page_objects"].update(roles[role].get("page_objects", {}))
            return merged

        out = scenario_dir(options.run_dir, self.name)
        async with scenario_client(
            client,
            policy=MOVE_CONTAINERS_POLICY,
            allowed_tools=self.tool_allowlist,
            run_dir=out,
            timeout_seconds=options.timeout,
            client_factory=MCPStdioClient,
        ) as active_client:
            move_started = time.perf_counter()
            options.progress.unit_started("case", f"{self.resource_type}-move", 1, 1)
            before = await capture_bundle(active_client)
            write_json(out / "before.json", before)
            current_source = find_snapshot_item(before, str(source["id"]))
            if current_source is None or current_source.get("resource_type") != self.resource_type:
                raise RunnerFailure("Typed container Move source is missing before execution.")
            selected = expected_copy_source_items(before, str(current_source["id"]), True)
            source_ids = [str(item["id"]) for item in selected]
            destination_name = f"Moved-{self.resource_type}-{run_safe_timestamp(args)}"

            plan_arguments = {
                f"{self.resource_type}_id": current_source["id"],
                "destination_parent_id": destination_notebook_id,
                "destination_name": destination_name,
            }
            planned = await active_client.call_tool(self.plan_tool, plan_arguments)
            write_json(out / "plan.json", planned)
            planned_ids = [
                str(item["id"])
                for item in planned.get("snapshots", {}).get("source", {}).get("resources", [])
            ]
            if planned.get("operation") != f"move_{self.resource_type}" or planned_ids != source_ids:
                raise InvariantFailure("Container Move plan selected the wrong typed subtree.")
            move_notebooks = planned.get("move_notebooks", {})
            if move_notebooks != {
                "source_notebook_id": source_notebook_id,
                "destination_notebook_id": destination_notebook_id,
                "cross_notebook": True,
            }:
                raise InvariantFailure("Container Move plan did not bind the two Notebook roles.")

            move_arguments = {
                f"{self.resource_type}_id": current_source["id"],
                "destination_parent_id": destination_notebook_id,
                "expected_name": display_name(current_source),
                "expected_parent_id": current_source["parent_id"],
                "expected_modified": current_source.get("modified"),
                "destination_name": destination_name,
                "plan_digest": planned["plan_digest"],
            }
            moved = await call_with_result_evidence(
                active_client,
                self.move_tool,
                move_arguments,
                out / "move-result.json",
            )
            report = moved.get("copy_report", {})
            id_map = report.get("id_map")
            if (
                report.get("verified") is not True
                or report.get("lossless") is not True
                or not isinstance(id_map, dict)
                or list(id_map) != source_ids
            ):
                raise InvariantFailure("Container Move Copy gate or id_map is incomplete.")
            if (
                moved.get("source_deleted_nonpermanently") is not True
                or moved.get("attempted_source_ids") != [str(current_source["id"])]
                or moved.get("deleted_source_ids") != [str(current_source["id"])]
                or moved.get("inactive_source_ids") != source_ids
                or moved.get("remaining_source_ids") != []
            ):
                raise InvariantFailure(
                    "Container Move did not report one safe root deletion and full subtree inactivity."
                )

            after = await capture_bundle(active_client)
            write_json(out / "after.json", after)
            after_ids = {str(item["id"]) for item in after.get("items", [])}
            if set(source_ids) & after_ids:
                raise InvariantFailure("A moved source subtree ID remains active.")
            assert_copy_mapping(
                before,
                after,
                str(current_source["id"]),
                destination_notebook_id,
                destination_name,
                moved,
                True,
            )
            destination_ids = {
                str(item["id"])
                for item in after["roles"]["destination"].get("items", [])
            }
            target_ids = [str(id_map[value]) for value in source_ids]
            if not set(target_ids).issubset(destination_ids):
                raise InvariantFailure("A container Move target escaped the destination Notebook.")
            position_evidence = assert_destination_position(
                moved,
                after,
                target_ids[0],
            )
            write_json(out / "destination-position-evidence.json", position_evidence)

            remaining = {
                "status": "cross_notebook_container_move_completed",
                "source_ids": source_ids,
                "target_ids": target_ids,
                "manual_cleanup_required": True,
                "reason": (
                    "The disposable source subtree was removed non-permanently after verified Copy; "
                    "the reconstructed target remains for inspection."
                ),
            }
            write_json(out / "restored.json", remaining)
            keep_worksite = bool(getattr(args, "keep_worksite", False))
            if keep_worksite:
                write_json(out / "worksite.json", remaining)
            result = {
                "scenario": self.name,
                "status": "passed",
                "source_ids": source_ids,
                "target_ids": target_ids,
                "source_deleted_nonpermanently": True,
                "restored": False,
                "worksite_preserved": keep_worksite,
                "remaining_state": remaining,
            }
            write_json(out / "result.json", result)
            options.progress.unit_completed(
                "case",
                f"{self.resource_type}-move",
                1,
                1,
                elapsed_seconds=time.perf_counter() - move_started,
            )
            render_report(options.run_dir)
            return result


__all__ = ["ContainerMoveScenario"]
