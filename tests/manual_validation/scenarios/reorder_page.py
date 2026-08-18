"""Page reorder-and-restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import ClientFailure, MCPStdioClient, WRITE_POLICY, scenario_client
from ..runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
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
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.reorder_page import RECIPE
from .common.dry_run import DryRunVariant
from .common.config import REORDER_PAGE_TOOLS
from .common.expected_rejection import expect_mutation_preflight_rejection
from .common.report import render_report


def page_predecessor(pages: list[dict[str, Any]], page_id: str) -> str:
    index = next((index for index, page in enumerate(pages) if page.get("id") == page_id), -1)
    if index < 0:
        raise RunnerFailure(f"Page is missing from snapshot: {page_id}")
    return "" if index == 0 else str(pages[index - 1]["id"])


async def _execute_reorder_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    target = resolve_manifest_item(manifest, "sibling_page")
    after_target = resolve_manifest_item(manifest, "parent_page")
    section = resolve_manifest_item(manifest, "reorder_section")
    out = scenario_dir(options.run_dir, "reorder-page")
    async with scenario_client(
        client,
        policy=WRITE_POLICY,
        allowed_tools=REORDER_PAGE_TOOLS,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        original = find_snapshot_item(before, target["id"])
        if original is None:
            raise RunnerFailure("Reorder target is not active.")
        parent = find_snapshot_item(before, after_target["id"])
        child_declared = resolve_manifest_item(manifest, "child_page")
        child = find_snapshot_item(before, child_declared["id"])
        if parent is None or child is None:
            raise RunnerFailure("Reorder scope-validation Parent/Child Pages are not active.")
        pages = sorted(
            [item for item in before["items"] if item.get("section_id") == section["id"]],
            key=lambda item: int(item["order"]),
        )
        await client.call_tool(
            "reorder_page",
            {
                "page_id": parent["id"],
                "expected_title": display_name(parent),
                "expected_section_id": section["id"],
                "after_page_id": original["id"],
                "page_level": 1,
                "expected_modified": parent.get("modified"),
                "include_subpages": False,
            },
        )
        root_only_after = await capture_snapshot(client, notebook_id)
        write_json(out / "after-root-only.json", root_only_after)
        root_only_pages = sorted(
            [
                item
                for item in root_only_after["items"]
                if item.get("section_id") == section["id"]
            ],
            key=lambda item: int(item["order"]),
        )
        protected_child = find_snapshot_item(root_only_after, child["id"])
        if (
            [item["id"] for item in root_only_pages]
            != [child["id"], original["id"], parent["id"]]
            or protected_child is None
            or int(protected_child.get("page_level", 0)) != 1
            or protected_child.get("parent_page_id") not in {None, ""}
            or before["page_hashes"] != root_only_after["page_hashes"]
        ):
            raise InvariantFailure(
                "Reorder include_subpages=false did not move only the root and protect its child."
            )
        moved_parent = find_snapshot_item(root_only_after, parent["id"])
        if moved_parent is None:
            raise RestoreFailure("Root-only Reorder lost its selected Parent Page.")
        await client.call_tool(
            "reorder_page",
            {
                "page_id": moved_parent["id"],
                "expected_title": display_name(moved_parent),
                "expected_section_id": section["id"],
                "after_page_id": "",
                "page_level": 1,
                "expected_modified": moved_parent.get("modified"),
                "include_subpages": False,
            },
        )
        root_before_child_restore = await capture_snapshot(client, notebook_id)
        child_to_restore = find_snapshot_item(root_before_child_restore, child["id"])
        restored_parent = find_snapshot_item(root_before_child_restore, parent["id"])
        if child_to_restore is None or restored_parent is None:
            raise RestoreFailure("Root-only Reorder could not bind its restore Pages.")
        await client.call_tool(
            "reorder_page",
            {
                "page_id": child_to_restore["id"],
                "expected_title": display_name(child_to_restore),
                "expected_section_id": section["id"],
                "after_page_id": restored_parent["id"],
                "page_level": 2,
                "expected_modified": child_to_restore.get("modified"),
                "include_subpages": False,
            },
        )
        root_only_restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored-root-only.json", root_only_restored)
        assert_restored(before, root_only_restored)

        subtree_parent = find_snapshot_item(root_only_restored, parent["id"])
        subtree_anchor = find_snapshot_item(root_only_restored, original["id"])
        if subtree_parent is None or subtree_anchor is None:
            raise RunnerFailure("Subtree Reorder could not bind its Parent/anchor Pages.")
        await client.call_tool(
            "reorder_page",
            {
                "page_id": subtree_parent["id"],
                "expected_title": display_name(subtree_parent),
                "expected_section_id": section["id"],
                "after_page_id": subtree_anchor["id"],
                "page_level": 1,
                "expected_modified": subtree_parent.get("modified"),
                "include_subpages": True,
            },
        )
        subtree_after = await capture_snapshot(client, notebook_id)
        write_json(out / "after-full-subtree.json", subtree_after)
        subtree_pages = sorted(
            [
                item
                for item in subtree_after["items"]
                if item.get("section_id") == section["id"]
            ],
            key=lambda item: int(item["order"]),
        )
        moved_child = find_snapshot_item(subtree_after, child["id"])
        if (
            [item["id"] for item in subtree_pages]
            != [original["id"], parent["id"], child["id"]]
            or moved_child is None
            or int(moved_child.get("page_level", 0)) != 2
            or moved_child.get("parent_page_id") != parent["id"]
            or before["page_hashes"] != subtree_after["page_hashes"]
        ):
            raise InvariantFailure(
                "Reorder include_subpages=true did not move the complete Page block."
            )
        moved_subtree_parent = find_snapshot_item(subtree_after, parent["id"])
        if moved_subtree_parent is None:
            raise RestoreFailure("Subtree Reorder lost its selected Parent Page.")
        await client.call_tool(
            "reorder_page",
            {
                "page_id": moved_subtree_parent["id"],
                "expected_title": display_name(moved_subtree_parent),
                "expected_section_id": section["id"],
                "after_page_id": "",
                "page_level": 1,
                "expected_modified": moved_subtree_parent.get("modified"),
                "include_subpages": True,
            },
        )
        subtree_restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored-full-subtree.json", subtree_restored)
        assert_restored(before, subtree_restored)

        original = find_snapshot_item(subtree_restored, target["id"])
        if original is None:
            raise RunnerFailure("Main Reorder target disappeared after scope validation.")
        pages = sorted(
            [
                item
                for item in subtree_restored["items"]
                if item.get("section_id") == section["id"]
            ],
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
        sort_result: dict[str, Any] = {"skipped_for_keep_worksite": True}
        expected_rejection: dict[str, Any] | None = None
        if not getattr(args, "keep_worksite", False):
            parent_after = find_snapshot_item(after, str(after_target["id"]))
            if parent_after is None:
                raise RunnerFailure("Sort parent Page disappeared after Reorder.")
            direct_children = [
                item
                for item in after["items"]
                if item.get("resource_type") == "page"
                and item.get("parent_page_id") == parent_after["id"]
            ]
            sort_result = await client.call_tool(
                "sort_children",
                {
                    "parent_id": parent_after["id"],
                    "expected_parent_name": display_name(parent_after),
                    "expected_parent_modified": parent_after.get("modified"),
                    "expected_child_ids": [str(item["id"]) for item in direct_children],
                    "key": "name",
                    "direction": "ascending",
                },
            )
            sorted_snapshot = await capture_snapshot(client, notebook_id)
            write_json(out / "sorted.json", sorted_snapshot)
            sorted_children = [
                item
                for item in sorted_snapshot["items"]
                if item.get("resource_type") == "page"
                and item.get("parent_page_id") == parent_after["id"]
            ]
            if [display_name(item) for item in sorted_children] != sorted(
                display_name(item) for item in sorted_children
            ):
                raise InvariantFailure("sort_children did not order direct Page children by name.")
            if snapshot_ids(after) != snapshot_ids(sorted_snapshot):
                raise InvariantFailure("sort_children changed a Page identity.")
            if after["page_hashes"] != sorted_snapshot["page_hashes"]:
                raise InvariantFailure("sort_children changed Page content.")
            sorted_parent = find_snapshot_item(sorted_snapshot, parent_after["id"])
            if sorted_parent is None:
                raise InvariantFailure("sort_children read-back omitted its parent Page.")
            expected_rejection = await expect_mutation_preflight_rejection(
                client,
                "sort_children",
                {
                    "child_type": "section",
                    "parent_id": sorted_parent["id"],
                    "expected_parent_name": display_name(sorted_parent),
                    "expected_parent_modified": sorted_parent.get("modified"),
                    "expected_child_ids": [str(item["id"]) for item in sorted_children],
                    "key": "name",
                    "direction": "ascending",
                },
                out / "expected-sort-rejection.json",
                label="page-parent-child-type-conflict",
                expected_message_fragment="conflicts",
            )
            rejected_snapshot = await capture_snapshot(client, notebook_id)
            write_json(out / "expected-sort-rejection-after.json", rejected_snapshot)
            assert_restored(sorted_snapshot, rejected_snapshot)
            after = rejected_snapshot
            changed = find_snapshot_item(after, original["id"])
        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_reorder_page",
                "target_ids": [original["id"]],
                "target_id": original["id"],
                "original_after_page_id": original_after,
                "current_after_page_id": page_predecessor(
                    sorted(
                        [item for item in after["items"] if item.get("section_id") == section["id"]],
                        key=lambda item: int(item["order"]),
                    ),
                    original["id"],
                ),
                "original_page_level": original_level,
                "current_page_level": args.page_level,
                "verified": validation_error is None,
                "manual_cleanup_required": True,
                "cleanup": (
                    f"Reorder Page {original['id']} after {original_after!r} at level "
                    f"{original_level} after inspection."
                ),
            }
            write_json(out / "worksite.json", worksite)
            if validation_error is not None:
                raise validation_error
            result = {
                "scenario": "reorder-page",
                "status": "passed",
                "target_id": original["id"],
                "temporary_after_page_id": after_target["id"],
                "temporary_page_level": args.page_level,
                "sort_result": sort_result,
                "include_subpages_validation": {
                    "root_only_protected_child_id": child["id"],
                    "full_subtree_ids": [parent["id"], child["id"]],
                    "content_unchanged": True,
                },
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result
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
            "scenario": "reorder-page",
            "status": "passed",
            "target_id": original["id"],
            "temporary_after_page_id": after_target["id"],
            "temporary_page_level": args.page_level,
            "sort_result": sort_result,
            "expected_rejection": expected_rejection,
            "include_subpages_validation": {
                "root_only_protected_child_id": child["id"],
                "full_subtree_ids": [parent["id"], child["id"]],
                "content_unchanged": True,
            },
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


@SCENARIO_REGISTRY.register
class ReorderPageScenario(Scenario):
    name = "reorder-page"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: validate Page reorder plus direct-child sort, prove conflicting child_type "
        "is rejected before mutation, restore or preserve, report, then close or keep."
    )
    included_in_all = True
    worksite_dry_run_action = "preserve-reordered-page"
    dry_run_variants = (DryRunVariant("level-one", ("--page-level", "1")),)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--page-level", type=int, default=2)

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await _execute_reorder_page(args, options, manifest, client=client)
