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
from .fixture_recipes.rename import RECIPE


_RENAME_CASES = (
    ("section", "section_target", "rename_section", "section_id"),
    (
        "section_group",
        "section_group_target",
        "rename_section_group",
        "section_group_id",
    ),
)


def _validate_transition(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str,
    new_name: str,
) -> dict[str, Any]:
    previous = find_snapshot_item(before, target_id)
    changed = find_snapshot_item(after, target_id)
    if previous is None or changed is None:
        raise InvariantFailure("Rename target identity disappeared during read-back.")
    if (
        display_name(changed) != new_name
        or changed.get("parent_id") != previous.get("parent_id")
    ):
        raise InvariantFailure(
            "Rename read-back did not preserve ID/parent and apply the requested name."
        )
    if snapshot_ids(before) != snapshot_ids(after):
        raise InvariantFailure("Rename changed one or more hierarchy object IDs.")
    before_by_id = {str(item["id"]): item for item in before["items"]}
    after_by_id = {str(item["id"]): item for item in after["items"]}
    for object_id, previous_item in before_by_id.items():
        if object_id == target_id:
            continue
        current_item = after_by_id[object_id]
        if (
            display_name(current_item) != display_name(previous_item)
            or current_item.get("parent_id") != previous_item.get("parent_id")
        ):
            raise InvariantFailure(
                "Rename changed an unrelated resource name or parent."
            )
    if page_topology(before) != page_topology(after):
        raise InvariantFailure(
            "Rename changed Page IDs, order, level, or parent relationships."
        )
    if before["page_hashes"] != after["page_hashes"]:
        raise InvariantFailure("Rename changed one or more Page XML hashes.")
    return changed


def _require_attempt_contract(
    response: dict[str, Any], operation: str
) -> dict[str, Any]:
    reconciliation = response.get("reconciliation")
    expected = {
        "state": "applied",
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "applied",
    }
    if not isinstance(reconciliation, dict) or any(
        reconciliation.get(key) != value for key, value in expected.items()
    ):
        raise InvariantFailure(
            f"{operation} omitted or violated its mutation attempt evidence."
        )
    return dict(reconciliation)


async def _execute_rename(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    declared_targets = {
        resource_type: resolve_manifest_item(manifest, target_key)
        for resource_type, target_key, _tool, _id_key in _RENAME_CASES
    }
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
        planned: list[dict[str, Any]] = []
        for resource_type, _target_key, tool, id_key in _RENAME_CASES:
            target = declared_targets[resource_type]
            current = find_snapshot_item(before, str(target["id"]))
            if current is None or current.get("resource_type") != resource_type:
                raise RunnerFailure(
                    f"Fixed Rename {resource_type} target is not active with its exact type."
                )
            original_name = display_name(current)
            new_name = args.new_name or f"{original_name}-Smoke-Renamed"
            if new_name == original_name:
                raise RunnerFailure(
                    "--new-name must differ from both fixed target names."
                )
            planned.append(
                {
                    "resource_type": resource_type,
                    "tool": tool,
                    "id_key": id_key,
                    "target_id": str(current["id"]),
                    "parent_id": current["parent_id"],
                    "original_name": original_name,
                    "new_name": new_name,
                }
            )

        current_snapshot = before
        completed: list[dict[str, Any]] = []
        validation_error: InvariantFailure | None = None
        for case in planned:
            current = find_snapshot_item(current_snapshot, case["target_id"])
            if current is None:
                raise RunnerFailure("Rename target disappeared before its fixed case ran.")
            forward = await client.call_tool(
                case["tool"],
                {
                    case["id_key"]: current["id"],
                    "new_name": case["new_name"],
                    "expected_name": case["original_name"],
                    "expected_parent_id": case["parent_id"],
                    "expected_modified": current.get("modified"),
                },
            )
            after = await capture_snapshot(client, notebook_id)
            write_json(out / f"{case['resource_type']}-after.json", after)
            completed_case = {
                **case,
                "forward_result": forward.get("item"),
                "forward_reconciliation": forward.get("reconciliation"),
            }
            completed.append(completed_case)
            try:
                completed_case["forward_reconciliation"] = _require_attempt_contract(
                    forward, case["tool"]
                )
                _validate_transition(
                    current_snapshot,
                    after,
                    target_id=case["target_id"],
                    new_name=case["new_name"],
                )
            except InvariantFailure as exc:
                validation_error = exc
                current_snapshot = after
                break
            current_snapshot = after

        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_fixed_rename_cases",
                "target_ids": [case["target_id"] for case in completed],
                "verified": validation_error is None,
                "manual_cleanup_required": True,
                "cleanup": [
                    (
                        f"Rename {case['resource_type']} {case['target_id']} back to "
                        f"{case['original_name']!r} after inspection."
                    )
                    for case in completed
                ],
            }
            write_json(out / "worksite.json", worksite)
            if validation_error is not None:
                raise validation_error
            result = {
                "scenario": "rename",
                "status": "passed",
                "target_ids": worksite["target_ids"],
                "cases": completed,
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        try:
            for case in reversed(completed):
                restore_target = find_snapshot_item(
                    current_snapshot, case["target_id"]
                )
                if restore_target is None:
                    raise RestoreFailure(
                        "Rename succeeded but an exact target was unavailable for restoration."
                    )
                restore_response = await client.call_tool(
                    case["tool"],
                    {
                        case["id_key"]: restore_target["id"],
                        "new_name": case["original_name"],
                        "expected_name": case["new_name"],
                        "expected_parent_id": case["parent_id"],
                        "expected_modified": restore_target.get("modified"),
                    },
                )
                current_snapshot = await capture_snapshot(client, notebook_id)
                case["restore_result"] = restore_response.get("item")
                case["restore_reconciliation"] = _require_attempt_contract(
                    restore_response, f"{case['tool']}:restore"
                )
                write_json(
                    out / f"{case['resource_type']}-restored.json",
                    current_snapshot,
                )
            restored = current_snapshot
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
            "target_ids": [case["target_id"] for case in completed],
            "cases": completed,
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class RenameScenario(Scenario):
    name = "rename"
    fixture_recipe = RECIPE
    help_text = "GATED: create, rename/restore or preserve, report, then close or keep."
    included_in_all = True
    worksite_dry_run_action = "preserve-both-renamed-targets"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
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
