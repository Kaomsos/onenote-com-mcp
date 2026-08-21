"""Cross-Notebook root-only and full-subtree Page Move safety scenario."""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, MOVE_PAGE_POLICY, scenario_client
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
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
from .common.config import MOVE_PAGE_TOOLS
from .common.copy_invariants import (
    assert_copy_mapping,
    assert_page_copy_fresh_ids,
    expected_copy_source_items,
)
from .common.copy_runtime import call_with_result_evidence
from .common.destination_position import assert_destination_position
from .common.page_readback import (
    assert_default_page_title_readback,
    assert_semantic_content_page_readback,
)
from .common.registry import SCENARIO_REGISTRY
from .common.report import render_report
from .common.specs import get_scenario_spec
from .fixture_recipes.move_page import RECIPE


async def _execute_move_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    validate_manifest_notebook(manifest, args.notebook_name)
    notebooks = manifest.get("notebooks")
    if not isinstance(notebooks, dict) or set(notebooks) != {"destination", "source"}:
        raise RunnerFailure("Move Page requires exact source/destination Notebook roles.")
    destination = resolve_manifest_item(manifest, "destination_section")
    cases = get_scenario_spec("move-page").execution_contract.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise RunnerFailure("Move Page requires its two declared cross-Notebook cases.")

    async def capture_bundle(active_client: MCPStdioClient) -> dict[str, Any]:
        roles = {
            role: await capture_snapshot(active_client, str(notebooks[role]["id"]))
            for role in ("source", "destination")
        }
        merged: dict[str, Any] = {
            "notebook_id": str(notebooks["source"]["id"]),
            "notebook_ids": {role: str(notebooks[role]["id"]) for role in roles},
            "roles": roles,
            "items": [],
            "page_hashes": {},
        }
        for role in ("source", "destination"):
            merged["items"].extend(roles[role].get("items", []))
            merged["page_hashes"].update(roles[role].get("page_hashes", {}))
        return merged

    out = scenario_dir(options.run_dir, "move-page")
    async with scenario_client(
        client,
        policy=MOVE_PAGE_POLICY,
        allowed_tools=MOVE_PAGE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        current_snapshot = await capture_bundle(client)
        write_json(out / "before.json", current_snapshot)
        case_results: list[dict[str, Any]] = []
        all_target_ids: list[str] = []

        for index, case in enumerate(cases, start=1):
            case_name = str(case["name"])
            case_started = time.perf_counter()
            options.progress.unit_started("case", case_name, index, len(cases))
            source = resolve_manifest_item(manifest, str(case["source_key"]))
            child = resolve_manifest_item(manifest, str(case["child_key"]))
            current_source = find_snapshot_item(current_snapshot, str(source["id"]))
            if current_source is None:
                raise RunnerFailure(f"Move source is missing before case '{case_name}'.")
            include_descendants = case.get("include_descendants") is True
            selected = expected_copy_source_items(
                current_snapshot,
                str(current_source["id"]),
                include_descendants,
            )
            expected_source_ids = [str(item["id"]) for item in selected]
            title_parameter = str(case.get("destination_title", "explicit"))
            if title_parameter not in {"omitted", "explicit-source-title"}:
                raise RunnerFailure(
                    "Move Page destination_title contract must be omitted or explicit-source-title."
                )
            destination_title = display_name(current_source)
            collision_keys = case.get("collision_anchor_keys")
            if (
                not isinstance(collision_keys, list)
                or not collision_keys
                or any(not str(key) for key in collision_keys)
            ):
                raise RunnerFailure(
                    f"Move Page case '{case_name}' must declare collision_anchor_keys."
                )
            collision_anchors = [
                resolve_manifest_item(manifest, str(key)) for key in collision_keys
            ]
            before = current_snapshot
            write_json(out / f"before-{case_name}.json", before)
            move_arguments = {
                "page_id": current_source["id"],
                "destination_section_id": destination["id"],
                "expected_title": display_name(current_source),
                "expected_section_id": current_source["section_id"],
                "expected_modified": current_source.get("modified"),
            }
            if title_parameter != "omitted":
                move_arguments["destination_title"] = destination_title
            if include_descendants:
                move_arguments["include_subpages"] = True
            moved = await call_with_result_evidence(
                client,
                "move_page",
                move_arguments,
                out / f"copy-result-{case_name}.json",
            )
            report = moved.get("copy_report", {})
            if report.get("verified") is not True or report.get("lossless") is not True:
                raise InvariantFailure(f"Move Copy gate failed for case '{case_name}'.")
            if moved.get("source_deleted_nonpermanently") is not True:
                raise InvariantFailure(f"Move did not report non-permanent deletion for '{case_name}'.")
            recycle_status = moved.get("recycle_bin_verification")
            recycled_source_ids = moved.get("recycled_source_ids")
            recycle_unverified_source_ids = moved.get("recycle_unverified_source_ids")
            expected_deleted = list(reversed(expected_source_ids))
            if recycle_status == "verified":
                if recycled_source_ids != expected_deleted or recycle_unverified_source_ids:
                    raise InvariantFailure(
                        f"Move recycle-bin verified evidence is inconsistent for '{case_name}'."
                    )
            elif recycle_status == "not_required_com_unavailable":
                if recycle_unverified_source_ids != expected_deleted or recycled_source_ids:
                    raise InvariantFailure(
                        f"Move recycle-bin unavailable evidence is inconsistent for '{case_name}'."
                    )
            else:
                raise InvariantFailure(
                    f"Move recycle-bin verification is not a known closed value for '{case_name}'."
                )
            if moved.get("include_descendants") is not include_descendants:
                raise InvariantFailure(f"Move result scope differs for case '{case_name}'.")
            semantic_readback = assert_semantic_content_page_readback(
                report,
                source_page_ids=expected_source_ids,
            )
            write_json(
                out / f"semantic-readback-{case_name}.json",
                semantic_readback,
            )
            title_readback = None
            if title_parameter == "omitted":
                title_readback = assert_default_page_title_readback(
                    report,
                    source_page_id=str(current_source["id"]),
                )
                write_json(
                    out / f"default-title-readback-{case_name}.json",
                    title_readback,
                )
            id_map = report.get("id_map")
            if not isinstance(id_map, dict) or list(id_map) != expected_source_ids:
                raise InvariantFailure(f"Move id_map scope differs for case '{case_name}'.")
            target_ids = [str(id_map[value]) for value in expected_source_ids]
            if moved.get("attempted_source_ids") != list(reversed(expected_source_ids)):
                raise InvariantFailure(f"Move deletion order is not leaf-to-root for '{case_name}'.")
            if moved.get("deleted_source_ids") != list(reversed(expected_source_ids)):
                raise InvariantFailure(f"Move deleted the wrong source IDs for '{case_name}'.")

            after = await capture_bundle(client)
            write_json(out / f"after-{case_name}.json", after)
            after_by_id = {str(item["id"]): item for item in after.get("items", [])}
            if set(expected_source_ids) & set(after_by_id):
                raise InvariantFailure(f"Moved source remains active for case '{case_name}'.")
            if any(
                target_id not in after_by_id
                or str(after_by_id[target_id].get("section_id")) != str(destination["id"])
                for target_id in target_ids
            ):
                raise InvariantFailure(f"Move target escaped the destination Notebook for '{case_name}'.")
            if display_name(after_by_id[target_ids[0]]) != destination_title:
                raise InvariantFailure(f"Move target title differs for case '{case_name}'.")
            if include_descendants:
                assert_copy_mapping(
                    before,
                    after,
                    str(current_source["id"]),
                    str(destination["id"]),
                    destination_title,
                    moved,
                    include_descendants=True,
                )
            assert_page_copy_fresh_ids(before, moved)
            before_hashes = before.get("page_hashes")
            after_hashes = after.get("page_hashes")
            stable_fields = (
                "resource_type",
                "name",
                "title",
                "parent_id",
                "section_id",
                "parent_page_id",
                "page_level",
                "order",
            )
            for collision_anchor in collision_anchors:
                anchor_id = str(collision_anchor["id"])
                anchor_before = find_snapshot_item(before, anchor_id)
                anchor_after = after_by_id.get(anchor_id)
                if anchor_before is None or anchor_after is None or any(
                    anchor_after.get(field) != anchor_before.get(field)
                    for field in stable_fields
                ):
                    raise InvariantFailure(
                        f"Move case '{case_name}' changed or reordered a collision anchor."
                    )
                if not isinstance(before_hashes, dict) or not isinstance(after_hashes, dict):
                    raise InvariantFailure(
                        f"Move case '{case_name}' is missing collision-anchor hash evidence."
                    )
                if before_hashes.get(anchor_id) != after_hashes.get(anchor_id):
                    raise InvariantFailure(
                        f"Move case '{case_name}' changed a collision-anchor content hash."
                    )
                if anchor_id in set(target_ids):
                    raise InvariantFailure(
                        f"Move case '{case_name}' reused a collision anchor as a target."
                    )
            position_evidence = assert_destination_position(
                moved,
                after,
                target_ids[0],
            )
            write_json(
                out / f"destination-position-evidence-{case_name}.json",
                position_evidence,
            )

            preservation = moved.get("preserved_descendants", {})
            if include_descendants:
                if preservation.get("preserved_descendant_ids"):
                    raise InvariantFailure("Subtree Move unexpectedly preserved a selected descendant.")
            else:
                child_before = find_snapshot_item(before, str(child["id"]))
                child_after = after_by_id.get(str(child["id"]))
                if child_before is None or child_after is None:
                    raise InvariantFailure("Root-only Move removed its excluded child.")
                if (
                    int(child_after.get("page_level", 0))
                    != int(current_source.get("page_level", 1))
                    or child_after.get("parent_page_id") != current_source.get("parent_page_id")
                    or after.get("page_hashes", {}).get(str(child["id"]))
                    != before.get("page_hashes", {}).get(str(child["id"]))
                ):
                    raise InvariantFailure("Root-only Move did not safely promote its excluded child.")
                if preservation.get("preserved_descendant_ids") != [str(child["id"])]:
                    raise InvariantFailure("Root-only Move preservation evidence is incomplete.")

            case_results.append(
                {
                    "case": case_name,
                    "parameter": "omitted" if not include_descendants else True,
                    "destination_title_parameter": title_parameter,
                    "default_title_readback": title_readback,
                    "semantic_content_readback": semantic_readback,
                    "effective_include_descendants": include_descendants,
                    "source_ids": expected_source_ids,
                    "target_ids": target_ids,
                    "copy_verified": True,
                    "source_deleted_nonpermanently": True,
                    "preserved_descendant_ids": preservation.get(
                        "preserved_descendant_ids", []
                    ),
                    "recycle_bin_verification": moved.get("recycle_bin_verification"),
                }
            )
            all_target_ids.extend(target_ids)
            current_snapshot = after
            options.progress.unit_completed(
                "case",
                case_name,
                index,
                len(cases),
                elapsed_seconds=time.perf_counter() - case_started,
            )

        write_json(out / "after.json", current_snapshot)
        remaining = {
            "status": "two_cross_notebook_moves_completed",
            "target_ids": all_target_ids,
            "cases": case_results,
            "manual_cleanup_required": True,
            "reason": (
                "Both disposable sources were removed non-permanently after verified Copy; "
                "the copied targets remain for inspection."
            ),
        }
        write_json(out / "restored.json", remaining)
        keep_worksite = bool(getattr(args, "keep_worksite", False))
        if keep_worksite:
            write_json(out / "worksite.json", remaining)
        result = {
            "scenario": "move-page",
            "status": "passed",
            "target_ids": all_target_ids,
            "restored": False,
            "worksite_preserved": keep_worksite,
            "source_deleted_nonpermanently": True,
            "remaining_state": remaining,
            "case_results": case_results,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class MovePageScenario(Scenario):
    name = "move-page"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: move two disposable Pages across Notebooks, once without descendants "
        "and once with the complete subtree; verify Copy then non-permanent source removal."
    )
    timeout_default = 1_800
    included_in_all = True
    worksite_dry_run_action = "preserve-two-cross-notebook-move-targets"

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_move_page(args, options, manifest, client=client)


__all__ = ["MovePageScenario"]
