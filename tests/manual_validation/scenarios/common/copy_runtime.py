"""Shared execution primitives for Page, Section, Group, and Notebook Copy."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    ClientFailure,
    MCPStdioClient,
    scenario_client,
)
from ...runtime import InvariantFailure, RestoreFailure, RunnerFailure, RuntimeOptions
from ...test_utils import (
    assert_restored,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    flatten_tree,
    resolve_manifest_item,
    scenario_dir,
    timestamp,
    validate_manifest_notebook,
    write_json,
)
from .config import (
    COPY_NOTEBOOK_PRESERVE_TOOLS,
    COPY_NOTEBOOK_TOOLS,
    COPY_PAGE_PRESERVE_TOOLS,
    COPY_PAGE_TOOLS,
    COPY_PRESERVE_TOOLS,
    COPY_TOOLS,
    RELAXED_COPY_CAPABILITIES,
    ROOT_PAGE_COPY_CAPABILITIES,
)
from .copy_invariants import assert_copy_fixture_capabilities, assert_copy_mapping
from .report import render_report
from .specs import get_scenario_spec


def copy_spec(
    scenario: str,
    manifest: dict[str, Any],
    run_dir: Path,
    *,
    keep_worksite: bool = False,
) -> dict[str, Any]:
    suffix = timestamp()
    copy_policy = COPY_NO_DELETE_POLICY if keep_worksite else COPY_POLICY
    copy_tools = COPY_PRESERVE_TOOLS if keep_worksite else COPY_TOOLS
    if scenario == "copy-page":
        execution_contract = get_scenario_spec(scenario).execution_contract
        page_tools = COPY_PAGE_PRESERVE_TOOLS if keep_worksite else COPY_PAGE_TOOLS
        cases = []
        for case in execution_contract.get("cases", []):
            declared_scope = case.get("include_descendants")
            cases.append(
                {
                    "name": str(case["name"]),
                    "destination_name": (
                        f"01-Root-Only-Copy-{suffix}"
                        if declared_scope == "omitted"
                        else f"02-Full-Subtree-Copy-{suffix}"
                    ),
                    "include_descendants": (
                        None if declared_scope == "omitted" else bool(declared_scope)
                    ),
                    "expected_page_count": int(case["expected_page_count"]),
                }
            )
        if len(cases) != 2:
            raise RunnerFailure("Copy Page execution contract must declare exactly two cases.")
        return {
            "source": resolve_manifest_item(manifest, "parent_page"),
            "destination": resolve_manifest_item(manifest, "disposable_section"),
            "tool": "copy_page",
            "cases": cases,
            "policy": copy_policy,
            "tools": page_tools,
        }
    if scenario == "copy-section":
        return {
            "source": resolve_manifest_item(manifest, "source_section"),
            "destination": resolve_manifest_item(manifest, "group_b"),
            "destination_name": f"Copy-Section-{suffix}",
            "tool": "copy_section",
            "policy": copy_policy,
            "tools": copy_tools,
        }
    if scenario == "copy-section-group":
        return {
            "source": resolve_manifest_item(manifest, "group_a"),
            "destination": manifest["notebook"],
            "destination_name": f"Copy-Group-{suffix}",
            "tool": "copy_section_group",
            "policy": copy_policy,
            "tools": copy_tools,
        }
    if scenario == "copy-notebook":
        disposable_targets = manifest.get("disposable_targets")
        allowlisted = (
            disposable_targets.get("notebook_copy_root")
            if isinstance(disposable_targets, dict)
            else None
        )
        expected_root = (run_dir / "notebook-copies").resolve()
        if not allowlisted or Path(allowlisted).resolve() != expected_root:
            raise RunnerFailure(
                "Manifest is missing the exact disposable Notebook Copy root; run create again."
            )
        return {
            "source": manifest["notebook"],
            "destination": None,
            "destination_name": f"Copy-Notebook-{suffix}",
            "destination_base_folder": str(expected_root),
            "tool": "copy_notebook",
            "policy": COPY_NO_DELETE_POLICY,
            "tools": (
                COPY_NOTEBOOK_PRESERVE_TOOLS if keep_worksite else COPY_NOTEBOOK_TOOLS
            ),
        }
    raise RunnerFailure(f"Unknown Copy scenario: {scenario}")


def copy_execute_arguments(
    spec: dict[str, Any],
    source: dict[str, Any],
    plan_digest: str,
) -> dict[str, Any]:
    common = {
        "plan_digest": plan_digest,
        "expected_modified": source.get("modified"),
    }
    tool = spec["tool"]
    if tool == "copy_page":
        arguments = {
            **common,
            "page_id": source["id"],
            "destination_section_id": spec["destination"]["id"],
            "expected_title": display_name(source),
            "expected_section_id": source["section_id"],
            "destination_title": spec["destination_name"],
        }
        if spec.get("include_descendants") is not None:
            arguments["include_descendants"] = spec["include_descendants"]
        return arguments
    if tool == "copy_section":
        id_key = "section_id"
    elif tool == "copy_section_group":
        id_key = "section_group_id"
    else:
        return {
            **common,
            "notebook_id": source["id"],
            "expected_name": display_name(source),
            "destination_name": spec["destination_name"],
            "destination_base_folder": spec["destination_base_folder"],
        }
    return {
        **common,
        id_key: source["id"],
        "destination_parent_id": spec["destination"]["id"],
        "expected_name": display_name(source),
        "expected_parent_id": source["parent_id"],
        "destination_name": spec["destination_name"],
    }


def plan_bound_before_snapshot(
    before: dict[str, Any],
    planned: dict[str, Any],
) -> dict[str, Any]:
    """Align runner evidence with the source snapshot protected by plan_digest."""

    planned_source = planned.get("source")
    source_snapshot = planned.get("snapshots", {}).get("source")
    if not isinstance(planned_source, dict) or not planned_source.get("id"):
        raise InvariantFailure("Copy plan is missing its typed source snapshot.")
    if not isinstance(source_snapshot, dict):
        raise InvariantFailure("Copy plan is missing source snapshot evidence.")
    planned_resources = source_snapshot.get("resources")
    planned_page_hashes = source_snapshot.get("page_hashes")
    if not isinstance(planned_resources, list) or not isinstance(planned_page_hashes, dict):
        raise InvariantFailure("Copy plan source snapshot is incomplete.")

    before_ids = {
        str(item["id"])
        for item in before.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    planned_by_id = {
        str(item["id"]): item
        for item in planned_resources
        if isinstance(item, dict) and item.get("id")
    }
    missing = sorted(set(planned_by_id) - before_ids)
    if missing:
        raise InvariantFailure(
            f"Copy plan source resources are missing from runner before evidence: {missing}"
        )
    if str(planned_source["id"]) not in planned_by_id:
        raise InvariantFailure("Copy plan source root is missing from its resource snapshot.")

    items = []
    for item in before.get("items", []):
        planned_item = planned_by_id.get(str(item.get("id", "")))
        if planned_item is None:
            items.append(item)
            continue
        rebound = dict(item)
        if "modified" in planned_item:
            rebound["modified"] = planned_item.get("modified")
        items.append(rebound)

    return {
        **before,
        "items": items,
        "page_hashes": dict(before.get("page_hashes", {})),
        "plan_binding": {
            "source_id": planned_source["id"],
            "source_snapshot_digest": planned.get("source_snapshot_digest"),
            "raw_page_hashes": planned_page_hashes,
            "include_descendants": planned.get("include_descendants"),
        },
    }


async def stable_copy_plan(
    client: MCPStdioClient,
    arguments: dict[str, Any],
    *,
    attempts_path: Path,
    plan_path: Path,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Require two consecutive identical read-only plans before Copy mutation."""

    previous_digest: str | None = None
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        planned = await client.call_tool("plan_copy", arguments)
        write_json(plan_path, planned)
        digest = str(planned.get("plan_digest", ""))
        source = planned.get("source", {})
        attempts.append(
            {
                "attempt": attempt,
                "plan_digest": digest,
                "source_snapshot_digest": planned.get("source_snapshot_digest"),
                "source_modified": (
                    source.get("modified") if isinstance(source, dict) else None
                ),
                "include_descendants": planned.get("include_descendants"),
            }
        )
        stabilized = bool(digest) and digest == previous_digest
        write_json(
            attempts_path,
            {
                "maximum_attempts": max_attempts,
                "stabilized": stabilized,
                "attempts": attempts,
            },
        )
        if stabilized:
            return planned
        previous_digest = digest
    raise InvariantFailure(
        "Copy source/destination plan did not stabilize across consecutive read-only "
        "snapshots; mutation was not attempted."
    )


async def cleanup_copy(
    client: MCPStdioClient,
    snapshot: dict[str, Any],
    copied: dict[str, Any],
) -> list[str]:
    id_map = copied.get("copy_report", {}).get("id_map", {})
    target_ids = set(id_map.values())
    targets = [item for item in snapshot.get("items", []) if item.get("id") in target_ids]
    if not targets:
        raise RestoreFailure("Copy returned IDs that were not found in the post-copy snapshot.")
    root = next((item for item in targets if item.get("id") == copied.get("item", {}).get("id")), None)
    if root is None:
        raise RestoreFailure("Copy target root was not found in the post-copy snapshot.")
    notebook_id = root.get("notebook_id")
    if not notebook_id:
        raise RestoreFailure("Copy target does not expose the containing Notebook ID needed for cleanup.")

    by_id = {item["id"]: item for item in targets}

    def container_depth(item: dict[str, Any]) -> int:
        depth = 0
        parent_id = item.get("parent_id")
        while parent_id in by_id:
            depth += 1
            parent_id = by_id[parent_id].get("parent_id")
        return depth

    cleanup_order = [
        *sorted(
            (item for item in targets if item["resource_type"] == "page"),
            key=lambda item: (str(item.get("section_id")), int(item.get("order", 0))),
            reverse=True,
        ),
        *sorted(
            (item for item in targets if item["resource_type"] == "section"),
            key=lambda item: (container_depth(item), str(item["id"])),
            reverse=True,
        ),
        *sorted(
            (item for item in targets if item["resource_type"] == "section_group"),
            key=lambda item: (container_depth(item), str(item["id"])),
            reverse=True,
        ),
    ]
    deleted: list[str] = []
    for target in cleanup_order:
        tree_result = await client.call_tool(
            "get_tree",
            {"root_id": notebook_id, "max_depth": 8},
        )
        current = next(
            (item for item in flatten_tree(tree_result["tree"]) if item.get("id") == target["id"]),
            None,
        )
        if current is None:
            raise RestoreFailure(
                f"Copy cleanup target '{target['id']}' disappeared before its explicit leaf-to-root step."
            )
        if current["resource_type"] == "page":
            tool = "delete_page"
            arguments = {
                "page_id": current["id"],
                "expected_title": display_name(current),
                "expected_section_id": current["section_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        else:
            tool = "delete_section" if current["resource_type"] == "section" else "delete_section_group"
            id_key = "section_id" if current["resource_type"] == "section" else "section_group_id"
            arguments = {
                id_key: current["id"],
                "expected_name": display_name(current),
                "expected_parent_id": current["parent_id"],
                "expected_modified": current.get("modified"),
                "permanently": False,
            }
        result = await client.call_tool(tool, arguments)
        if result.get("permanently") is not False:
            raise RestoreFailure("Copy cleanup did not explicitly confirm permanently=false.")
        deleted.append(current["id"])
    return deleted


async def call_with_result_evidence(
    client: MCPStdioClient,
    tool: str,
    arguments: dict[str, Any],
    evidence_path: Path,
) -> dict[str, Any]:
    """Persist both successful and structured partial mutation responses."""

    try:
        result = await client.call_tool(tool, arguments)
    except ClientFailure as exc:
        if exc.envelope is not None:
            write_json(evidence_path, exc.envelope)
        raise
    write_json(evidence_path, result)
    return result


async def execute_copy_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    """Execute and independently verify default root-only and explicit subtree Copy."""

    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    spec = copy_spec(
        "copy-page",
        manifest,
        options.run_dir,
        keep_worksite=keep_worksite,
    )
    cases = spec.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise RunnerFailure("Copy Page requires its two declared execution cases.")
    out = scenario_dir(options.run_dir, "copy-page")
    async with scenario_client(
        client,
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        original_before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", original_before)
        current_snapshot = original_before
        copied_results: list[dict[str, Any]] = []
        case_results: list[dict[str, Any]] = []
        plan_index: list[dict[str, Any]] = []

        for case in cases:
            case_name = str(case["name"])
            include_descendants = case.get("include_descendants")
            effective_scope = include_descendants is True
            pre_plan_source = find_snapshot_item(current_snapshot, spec["source"]["id"])
            if pre_plan_source is None:
                raise RunnerFailure(
                    f"Manifest Copy source is not active before case '{case_name}'."
                )
            case_spec = {
                **spec,
                "destination_name": str(case["destination_name"]),
                "include_descendants": include_descendants,
            }
            plan_arguments = {
                "source_id": pre_plan_source["id"],
                "destination_parent_id": spec["destination"]["id"],
                "destination_name": case_spec["destination_name"],
            }
            if include_descendants is not None:
                plan_arguments["include_descendants"] = include_descendants
            planned = await stable_copy_plan(
                client,
                plan_arguments,
                attempts_path=out / f"plan-attempts-{case_name}.json",
                plan_path=out / f"plan-{case_name}.json",
            )
            if planned.get("include_descendants") is not effective_scope:
                raise InvariantFailure(
                    f"Page Copy plan scope differs from case '{case_name}'."
                )
            if effective_scope:
                assert_copy_fixture_capabilities(planned, RELAXED_COPY_CAPABILITIES)
            else:
                assert_copy_fixture_capabilities(
                    planned,
                    ROOT_PAGE_COPY_CAPABILITIES,
                    include_automated_defaults=False,
                )
            case_before = plan_bound_before_snapshot(current_snapshot, planned)
            write_json(out / f"before-{case_name}.json", case_before)
            if find_snapshot_item(case_before, str(planned["source"]["id"])) is None:
                raise InvariantFailure(
                    f"Plan-bound Copy source is missing before case '{case_name}'."
                )
            current_source = dict(planned["source"])
            copied = await call_with_result_evidence(
                client,
                "copy_page",
                copy_execute_arguments(
                    case_spec,
                    current_source,
                    str(planned["plan_digest"]),
                ),
                out / f"copy-result-{case_name}.json",
            )
            report = copied.get("copy_report", {})
            if report.get("verified") is not True:
                raise InvariantFailure(
                    f"Copy case '{case_name}' did not report verified read-back."
                )
            id_map = report.get("id_map", {})
            target = copied.get("item")
            if (
                not isinstance(target, dict)
                or target.get("resource_type") != "page"
                or target.get("id") not in id_map.values()
            ):
                raise InvariantFailure(
                    f"Copy case '{case_name}' did not return its mapped Page root."
                )
            if len(id_map) != int(case["expected_page_count"]):
                raise InvariantFailure(
                    f"Copy case '{case_name}' mapped {len(id_map)} Pages; "
                    f"expected {case['expected_page_count']}."
                )

            case_after = await capture_snapshot(client, notebook_id)
            write_json(out / f"after-{case_name}.json", case_after)
            assert_copy_mapping(
                case_before,
                case_after,
                current_source["id"],
                spec["destination"]["id"],
                case_spec["destination_name"],
                copied,
                include_descendants=effective_scope,
            )
            for source_id, source_hash in case_before["page_hashes"].items():
                if case_after["page_hashes"].get(source_id) != source_hash:
                    raise InvariantFailure(
                        f"Copy case '{case_name}' changed a pre-existing Page XML hash."
                    )
            copied_results.append(copied)
            case_result = {
                "case": case_name,
                "parameter": (
                    "omitted" if include_descendants is None else include_descendants
                ),
                "effective_include_descendants": effective_scope,
                "destination_name": case_spec["destination_name"],
                "target_id": copied.get("item", {}).get("id"),
                "mapped_page_count": len(id_map),
                "copy_report": report,
            }
            case_results.append(case_result)
            plan_index.append(
                {
                    "case": case_name,
                    "parameter": case_result["parameter"],
                    "effective_include_descendants": effective_scope,
                    "plan_digest": planned["plan_digest"],
                    "source_snapshot_digest": planned.get("source_snapshot_digest"),
                }
            )
            current_snapshot = case_after

        write_json(out / "plans.json", {"cases": plan_index})
        write_json(out / "after.json", current_snapshot)
        target_ids = [
            str(target_id)
            for copied in copied_results
            for target_id in copied.get("copy_report", {}).get("id_map", {}).values()
        ]
        target_root_ids = [
            str(copied.get("item", {}).get("id")) for copied in copied_results
        ]
        if keep_worksite:
            remaining = {
                "status": "preserved_active_for_manual_inspection",
                "target_ids": target_ids,
                "target_root_ids": target_root_ids,
                "cases": case_results,
                "manual_cleanup_required": True,
                "reason": (
                    "--keep-worksite preserved both verified Page Copy scope targets."
                ),
            }
            write_json(out / "worksite.json", remaining)
            result = {
                "scenario": "copy-page",
                "status": "passed",
                "target_ids": target_ids,
                "restored": False,
                "worksite_preserved": True,
                "cleanup_deleted_ids": [],
                "remaining_state": remaining,
                "case_results": case_results,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        deleted_ids: list[str] = []
        for copied in reversed(copied_results):
            deleted_ids.extend(await cleanup_copy(client, current_snapshot, copied))
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(original_before, restored)
        result = {
            "scenario": "copy-page",
            "status": "passed",
            "target_ids": target_ids,
            "restored": True,
            "worksite_preserved": False,
            "cleanup_deleted_ids": deleted_ids,
            "case_results": case_results,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def execute_copy(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    if args.scenario == "copy-page":
        return await execute_copy_page(
            args,
            options,
            manifest,
            client=client,
        )
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    spec = copy_spec(
        args.scenario,
        manifest,
        options.run_dir,
        keep_worksite=keep_worksite,
    )
    if args.scenario == "copy-notebook":
        Path(spec["destination_base_folder"]).mkdir(parents=True, exist_ok=True)
    out = scenario_dir(options.run_dir, args.scenario)
    async with scenario_client(
        client,
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        pre_plan_source = find_snapshot_item(before, spec["source"]["id"])
        if pre_plan_source is None:
            raise RunnerFailure("Manifest Copy source is not active in the current snapshot.")
        plan_arguments = {
            "source_id": pre_plan_source["id"],
            "destination_name": spec["destination_name"],
        }
        if spec["destination"] is not None:
            plan_arguments["destination_parent_id"] = spec["destination"]["id"]
        if spec.get("destination_base_folder"):
            plan_arguments["destination_base_folder"] = spec["destination_base_folder"]
        if "include_descendants" in spec:
            plan_arguments["include_descendants"] = spec["include_descendants"]
        planned = await stable_copy_plan(
            client,
            plan_arguments,
            attempts_path=out / "plan-attempts.json",
            plan_path=out / "plan.json",
        )
        if spec["tool"] == "copy_page":
            if planned.get("include_descendants") is not spec["include_descendants"]:
                raise InvariantFailure(
                    "Page Copy plan scope differs from the scenario's fixed execution scope."
                )
            assert_copy_fixture_capabilities(
                planned,
                ROOT_PAGE_COPY_CAPABILITIES,
                include_automated_defaults=False,
            )
        else:
            assert_copy_fixture_capabilities(planned, RELAXED_COPY_CAPABILITIES)
        before = plan_bound_before_snapshot(before, planned)
        write_json(out / "before.json", before)
        if find_snapshot_item(before, str(planned["source"]["id"])) is None:
            raise InvariantFailure("Plan-bound Copy source is missing from before evidence.")
        current = dict(planned["source"])
        copied = await call_with_result_evidence(
            client,
            spec["tool"],
            copy_execute_arguments(spec, current, planned["plan_digest"]),
            out / "copy-result.json",
        )
        if copied.get("copy_report", {}).get("verified") is not True:
            raise InvariantFailure("Copy response did not report successful read-back verification.")

        if args.scenario == "copy-notebook":
            target = copied.get("item")
            if not isinstance(target, dict) or target.get("resource_type") != "notebook":
                raise InvariantFailure("Notebook Copy did not return a typed target Notebook.")
            actual_target_path = str(copied.get("destination_path", ""))
            planned_target_path = str(planned["destination"]["target_path"])
            if not actual_target_path or actual_target_path.casefold() != planned_target_path.casefold():
                raise InvariantFailure("Notebook Copy result path differs from the manifest-scoped plan.")
            target_snapshot = await capture_snapshot(client, target["id"])
            write_json(out / "after.json", target_snapshot)
            assert_copy_mapping(
                before,
                target_snapshot,
                current["id"],
                None,
                spec["destination_name"],
                copied,
                include_descendants=spec.get("include_descendants", True),
            )
            if keep_worksite:
                remaining = {
                    "status": "preserved_open_for_manual_inspection",
                    "target_id": target["id"],
                    "target_ids": list(copied.get("created_ids", [])),
                    "target_path": actual_target_path,
                    "manual_cleanup_required": True,
                    "reason": "--keep-worksite preserved the copied Notebook for UI inspection.",
                }
                write_json(out / "worksite.json", remaining)
            else:
                closed = await client.call_tool(
                    "close_notebook",
                    {
                        "notebook_id": target["id"],
                        "expected_name": display_name(target),
                        "expected_modified": target.get("modified"),
                    },
                )
                remaining = {
                    "status": "closed_not_deleted",
                    "target_id": target["id"],
                    "target_path": actual_target_path,
                    "close_result": closed,
                    "reason": "OneNote COM exposes CloseNotebook, not typed Notebook deletion.",
                }
                write_json(out / "restored.json", remaining)
            result = {
                "scenario": args.scenario,
                "status": "passed",
                "target_id": target["id"],
                "restored": False,
                "worksite_preserved": keep_worksite,
                "remaining_state": remaining,
                "copy_report": copied["copy_report"],
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        assert_copy_mapping(
            before,
            after,
            current["id"],
            spec["destination"]["id"],
            spec["destination_name"],
            copied,
            include_descendants=spec.get("include_descendants", True),
        )
        for source_id, source_hash in before["page_hashes"].items():
            if after["page_hashes"].get(source_id) != source_hash:
                raise InvariantFailure("Copy changed an existing source Page XML hash.")
        if keep_worksite:
            target_ids = list(copied.get("copy_report", {}).get("id_map", {}).values())
            remaining = {
                "status": "preserved_active_for_manual_inspection",
                "target_id": copied.get("item", {}).get("id"),
                "target_ids": target_ids,
                "manual_cleanup_required": True,
                "reason": "--keep-worksite skipped Copy target cleanup after verified read-back.",
            }
            write_json(out / "worksite.json", remaining)
            result = {
                "scenario": args.scenario,
                "status": "passed",
                "target_id": copied.get("item", {}).get("id"),
                "restored": False,
                "worksite_preserved": True,
                "cleanup_deleted_ids": [],
                "remaining_state": remaining,
                "copy_report": copied["copy_report"],
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result
        deleted_ids = await cleanup_copy(client, after, copied)
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(before, restored)
        result = {
            "scenario": args.scenario,
            "status": "passed",
            "target_id": copied.get("item", {}).get("id"),
            "restored": True,
            "worksite_preserved": False,
            "cleanup_deleted_ids": deleted_ids,
            "copy_report": copied["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


__all__ = [
    "call_with_result_evidence",
    "cleanup_copy",
    "copy_execute_arguments",
    "copy_spec",
    "execute_copy",
    "execute_copy_page",
]
