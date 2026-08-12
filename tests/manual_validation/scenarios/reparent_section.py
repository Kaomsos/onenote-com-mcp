"""Three-way same-Notebook Section reparent-and-restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import (
    ClientFailure,
    MCPStdioClient,
    REPARENT_POLICY,
    scenario_client,
)
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
from .common.config import REPARENT_SECTION_TOOLS
from .common.destination_position import assert_destination_position
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.reparent_section import RECIPE
from .common.report import render_report


REPARENT_PLANS = (
    (
        "notebook-to-section-group",
        "notebook_to_group_section",
        None,
        "notebook_to_group_destination",
    ),
    (
        "section-group-to-notebook",
        "group_to_notebook_section",
        "group_to_notebook_source",
        None,
    ),
    (
        "section-group-to-section-group",
        "group_to_group_section",
        "group_to_group_source",
        "group_to_group_destination",
    ),
)


def _parent_id(
    manifest: dict[str, Any],
    notebook_id: str,
    key: str | None,
) -> str:
    if key is None:
        return notebook_id
    return str(resolve_manifest_item(manifest, key)["id"])


def _validate_reparent_state(
    before: dict[str, Any],
    after: dict[str, Any],
    operations: list[dict[str, str]],
) -> None:
    if snapshot_ids(before) != snapshot_ids(after):
        raise InvariantFailure("Reparent changed one or more hierarchy object IDs.")
    if before["page_hashes"] != after["page_hashes"]:
        raise InvariantFailure("Reparent changed one or more Page content hashes.")
    for operation in operations:
        reparented = find_snapshot_item(after, operation["section_id"])
        if reparented is None or reparented.get("parent_id") != operation["destination_parent_id"]:
            raise InvariantFailure(
                f"{operation['case']} did not preserve the Section ID and apply its destination parent."
            )
        if page_topology(before, operation["section_id"]) != page_topology(
            after, operation["section_id"]
        ):
            raise InvariantFailure(
                f"{operation['case']} changed Page IDs, order, level, or parent relationships."
            )


async def _execute_reparent_section(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    out = scenario_dir(options.run_dir, "reparent-section")
    async with scenario_client(
        client,
        policy=REPARENT_POLICY,
        allowed_tools=REPARENT_SECTION_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current_snapshot = before
        operations: list[dict[str, str]] = []

        for index, (case, section_key, source_key, destination_key) in enumerate(
            REPARENT_PLANS, start=1
        ):
            declared = resolve_manifest_item(manifest, section_key)
            source_parent_id = _parent_id(manifest, notebook_id, source_key)
            destination_parent_id = _parent_id(
                manifest, notebook_id, destination_key
            )
            current = find_snapshot_item(current_snapshot, declared["id"])
            if current is None or current.get("parent_id") != source_parent_id:
                raise RunnerFailure(
                    f"{case} source Section is not under its declared parent; "
                    "refusing to guess recovery state."
                )
            response = await client.call_tool(
                "reparent_section",
                {
                    "section_id": current["id"],
                    "destination_parent_id": destination_parent_id,
                    "expected_name": display_name(current),
                    "expected_parent_id": source_parent_id,
                    "expected_modified": current.get("modified"),
                },
            )
            write_json(out / f"mutation-response-{index}.json", response)
            operations.append(
                {
                    "case": case,
                    "section_id": str(current["id"]),
                    "section_name": display_name(current),
                    "source_parent_id": source_parent_id,
                    "destination_parent_id": destination_parent_id,
                }
            )
            current_snapshot = await capture_snapshot(client, notebook_id)
            write_json(out / f"forward-{index}.json", current_snapshot)
            position_evidence = assert_destination_position(
                response,
                current_snapshot,
                str(current["id"]),
            )
            write_json(
                out / f"destination-position-evidence-{index}.json",
                position_evidence,
            )
            operations[-1]["destination_position"] = position_evidence
            _validate_reparent_state(before, current_snapshot, operations)

        after = current_snapshot
        write_json(out / "after.json", after)
        validation_error: InvariantFailure | None = None
        try:
            _validate_reparent_state(before, after, operations)
        except InvariantFailure as exc:
            validation_error = exc

        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_reparent",
                "target_ids": [operation["section_id"] for operation in operations],
                "operations": operations,
                "verified": validation_error is None,
                "manual_cleanup_required": True,
                "cleanup": [
                    (
                        f"Reparent Section {operation['section_id']} from parent "
                        f"{operation['destination_parent_id']} back to parent "
                        f"{operation['source_parent_id']} after inspection."
                    )
                    for operation in reversed(operations)
                ],
            }
            write_json(out / "worksite.json", worksite)
            if validation_error is not None:
                raise validation_error
            result = {
                "scenario": "reparent-section",
                "status": "passed",
                "operations": operations,
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
                "warning": (
                    "This validates one installed OneNote/Office combination, "
                    "not universal COM behavior."
                ),
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        try:
            restore_snapshot = after
            for index, operation in enumerate(reversed(operations), start=1):
                restore_target = find_snapshot_item(
                    restore_snapshot, operation["section_id"]
                )
                if restore_target is None:
                    raise RestoreFailure(
                        f"{operation['case']} target disappeared before restoration."
                    )
                await client.call_tool(
                    "reparent_section",
                    {
                        "section_id": restore_target["id"],
                        "destination_parent_id": operation["source_parent_id"],
                        "expected_name": operation["section_name"],
                        "expected_parent_id": operation["destination_parent_id"],
                        "expected_modified": restore_target.get("modified"),
                    },
                )
                restore_snapshot = await capture_snapshot(client, notebook_id)
                write_json(out / f"restore-{index}.json", restore_snapshot)
            restored = restore_snapshot
            write_json(out / "restored.json", restored)
            assert_restored(before, restored)
        except (ClientFailure, RunnerFailure) as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Reparent succeeded but restoration failed: {exc}") from exc

        if validation_error is not None:
            raise validation_error
        result = {
            "scenario": "reparent-section",
            "status": "passed",
            "operations": operations,
            "restored": True,
            "worksite_preserved": False,
            "warning": (
                "This validates one installed OneNote/Office combination, "
                "not universal COM behavior."
            ),
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class ReparentSectionScenario(Scenario):
    name = "reparent-section"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: validate Notebook→SectionGroup, SectionGroup→Notebook, and "
        "SectionGroup→SectionGroup Section reparent operations, then restore or preserve."
    )
    included_in_all = True
    worksite_dry_run_action = "preserve-reparented-section"
    capability_assessment = {
        "capability_status": "experimental",
        "validation_status": "passed",
        "reason": (
            "A user-run scenario confirmed all three same-Notebook Section parent "
            "transitions while preserving Section/Page identity, topology, and content."
        ),
    }

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_reparent_section(args, options, manifest, client=client)
