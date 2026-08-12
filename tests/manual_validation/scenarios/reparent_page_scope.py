"""Human-gated Page reparent root-only/full-subtree scope scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, REPARENT_POLICY, scenario_client
from ..runtime import InvariantFailure, RuntimeOptions
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
from .common.config import REPARENT_PAGE_TOOLS
from .common.destination_position import assert_destination_position
from .common.registry import SCENARIO_REGISTRY
from .common.report import render_report
from .fixture_recipes.reparent_page_scope import RECIPE


def _section_pages(snapshot: dict[str, Any], section_id: str) -> list[dict[str, Any]]:
    return sorted(
        (
            item
            for item in snapshot.get("items", [])
            if item.get("resource_type") == "page" and item.get("section_id") == section_id
        ),
        key=lambda item: int(item.get("order", 0)),
    )


@SCENARIO_REGISTRY.register
class ReparentPageScopeScenario(Scenario):
    name = "reparent-page-scope"
    fixture_recipe = RECIPE
    help_text = (
        "EXPERIMENTAL: validate root-only-default and full-subtree Page reparent scope."
    )
    included_in_all = False
    worksite_dry_run_action = "preserve-reparent-page-scope-result"
    capability_assessment = {
        "capability_status": "experimental",
        "validation_status": "pending",
        "reason": "The new two-case Page scope contract requires an explicit user-run scenario.",
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
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        source = resolve_manifest_item(manifest, "source_section")
        destination = resolve_manifest_item(manifest, "destination_section")
        out = scenario_dir(options.run_dir, self.name)
        cases = (
            {
                "name": "root-only-default",
                "target_key": "root_only_selected",
                "selected_keys": ("root_only_selected",),
                "preserved_keys": ("root_only_child", "root_only_grandchild"),
                "include_descendants": False,
            },
            {
                "name": "full-subtree",
                "target_key": "subtree_selected",
                "selected_keys": (
                    "subtree_selected",
                    "subtree_child_a",
                    "subtree_grandchild",
                    "subtree_child_b",
                ),
                "preserved_keys": (),
                "include_descendants": True,
            },
        )

        async with scenario_client(
            client,
            policy=REPARENT_POLICY,
            allowed_tools=REPARENT_PAGE_TOOLS,
            run_dir=out,
            timeout_seconds=options.timeout,
            client_factory=MCPStdioClient,
        ) as active_client:
            current_snapshot = await capture_snapshot(active_client, notebook_id)
            write_json(out / "before.json", current_snapshot)
            results: list[dict[str, Any]] = []

            for case in cases:
                name = str(case["name"])
                before = current_snapshot
                write_json(out / f"before-{name}.json", before)
                target_declared = resolve_manifest_item(manifest, str(case["target_key"]))
                target = find_snapshot_item(before, str(target_declared["id"]))
                if target is None or target.get("section_id") != source["id"]:
                    raise InvariantFailure(f"{name} selected Page is absent from the source Section.")
                selected_before = [
                    find_snapshot_item(
                        before,
                        str(resolve_manifest_item(manifest, key)["id"]),
                    )
                    for key in case["selected_keys"]
                ]
                if any(item is None for item in selected_before):
                    raise InvariantFailure(f"{name} selected scope is incomplete before mutation.")
                selected_ids = [str(item["id"]) for item in selected_before if item is not None]
                preserved_before = [
                    find_snapshot_item(
                        before,
                        str(resolve_manifest_item(manifest, key)["id"]),
                    )
                    for key in case["preserved_keys"]
                ]
                arguments = {
                    "page_id": target["id"],
                    "destination_section_id": destination["id"],
                    "expected_title": display_name(target),
                    "expected_section_id": source["id"],
                    "expected_modified": target.get("modified"),
                }
                if case["include_descendants"]:
                    arguments["include_descendants"] = True
                write_json(out / f"request-{name}.json", arguments)
                response = await active_client.call_tool("reparent_page", arguments)
                write_json(out / f"mutation-response-{name}.json", response)
                current_snapshot = await capture_snapshot(active_client, notebook_id)
                write_json(out / f"after-{name}.json", current_snapshot)

                if response.get("include_descendants") is not case["include_descendants"]:
                    raise InvariantFailure(f"{name} response reported the wrong Page scope.")
                id_map = response.get("id_map")
                if not isinstance(id_map, dict) or any(
                    source_id not in id_map for source_id in selected_ids
                ):
                    raise InvariantFailure(f"{name} response id_map is incomplete for selected Pages.")
                preserved_page_ids = {
                    str(item["id"]) for item in preserved_before if item is not None
                }
                if preserved_page_ids & set(id_map):
                    raise InvariantFailure(
                        "root-only response id_map incorrectly includes excluded descendants."
                    )
                target_ids = [str(id_map[source_id]) for source_id in selected_ids]
                if len(target_ids) != len(set(target_ids)):
                    raise InvariantFailure(f"{name} Page ID mapping is not injective.")
                after_by_id = {
                    str(item["id"]): item for item in current_snapshot.get("items", [])
                }
                target_pages = [after_by_id.get(target_id) for target_id in target_ids]
                if any(item is None for item in target_pages):
                    raise InvariantFailure(f"{name} destination scope is incomplete.")
                root_level = int(target["page_level"])
                expected_target_parents: dict[str, str | None] = {}
                stack: list[tuple[int, str]] = []
                for source_page, target_page in zip(
                    selected_before, target_pages, strict=True
                ):
                    assert source_page is not None and target_page is not None
                    expected_level = int(source_page["page_level"]) - root_level + 1
                    while stack and stack[-1][0] >= expected_level:
                        stack.pop()
                    expected_parent_id = stack[-1][1] if stack else None
                    if (
                        target_page.get("section_id") != destination["id"]
                        or int(target_page.get("page_level", 0)) != expected_level
                        or target_page.get("parent_page_id") not in {
                            expected_parent_id,
                            "" if expected_parent_id is None else expected_parent_id,
                        }
                        or current_snapshot["page_reparent_hashes"].get(str(target_page["id"]))
                        != before["page_reparent_hashes"].get(str(source_page["id"]))
                    ):
                        raise InvariantFailure(f"{name} changed selected Page topology/content.")
                    expected_target_parents[str(target_page["id"])] = expected_parent_id
                    stack.append((expected_level, str(target_page["id"])))

                if case["include_descendants"]:
                    destination_pages = _section_pages(current_snapshot, str(destination["id"]))
                    target_indexes = [
                        next(
                            index
                            for index, item in enumerate(destination_pages)
                            if str(item["id"]) == target_id
                        )
                        for target_id in target_ids
                    ]
                    if target_indexes != list(
                        range(target_indexes[0], target_indexes[0] + len(target_indexes))
                    ):
                        raise InvariantFailure("full-subtree target Pages are not contiguous.")
                else:
                    preserved_ids = [str(item["id"]) for item in preserved_before if item]
                    if response.get("preserved_descendants", {}).get(
                        "preserved_descendant_ids"
                    ) != preserved_ids:
                        raise InvariantFailure("root-only preservation evidence is incomplete.")
                    for old in preserved_before:
                        assert old is not None
                        current = after_by_id.get(str(old["id"]))
                        if (
                            current is None
                            or current.get("section_id") != source["id"]
                            or int(current.get("page_level", 0))
                            != int(old.get("page_level", 0)) - 1
                            or current_snapshot["page_hashes"].get(str(old["id"]))
                            != before["page_hashes"].get(str(old["id"]))
                        ):
                            raise InvariantFailure(
                                "root-only excluded descendant was not safely promoted."
                            )

                position = assert_destination_position(
                    response,
                    current_snapshot,
                    target_ids[0],
                )
                if "page_level" in position or len(
                    [key for key in response if "position" in key]
                ) != 1:
                    raise InvariantFailure(
                        f"{name} response exposed Page level or descendant position fields."
                    )
                write_json(out / f"destination-position-evidence-{name}.json", position)
                results.append(
                    {
                        "case": name,
                        "source_ids": selected_ids,
                        "target_ids": target_ids,
                        "destination_position": position,
                        "target_parent_page_ids": expected_target_parents,
                    }
                )

            remaining = {
                "status": "verified_non_restored_scope_result",
                "cases": results,
                "manual_cleanup_required": True,
                "reason": (
                    "Root-only intentionally promoted excluded descendants; no Reorder permission "
                    "was added to reconstruct the original disposable fixture."
                ),
            }
            write_json(out / "restored.json", remaining)
            if getattr(args, "keep_worksite", False):
                write_json(out / "worksite.json", remaining)
            result = {
                "scenario": self.name,
                "status": "passed",
                "cases": results,
                "restored": False,
                "worksite_preserved": bool(getattr(args, "keep_worksite", False)),
                "remaining_state": remaining,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result


__all__ = ["ReparentPageScopeScenario"]
