"""Shared execution primitives for Page, Section, Group, and Notebook Copy."""

from __future__ import annotations

import argparse
from pathlib import Path
import time
from typing import Any, Mapping

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
    mathml_structure_projection,
    read_json,
    resolve_manifest_item,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
)
from ...run_identity import new_run_identity, run_safe_timestamp
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
from .destination_position import assert_destination_position
from .copy_invariants import (
    assert_copy_fixture_capabilities,
    assert_copy_mapping,
    assert_copy_page_restored,
    assert_pages_unchanged,
)
from .report import render_report
from .specs import get_scenario_spec


def copy_spec(
    scenario: str,
    manifest: dict[str, Any],
    run_dir: Path,
    *,
    keep_worksite: bool = False,
    name_suffix: str | None = None,
) -> dict[str, Any]:
    suffix = name_suffix or new_run_identity().safe_timestamp
    copy_policy = COPY_NO_DELETE_POLICY if keep_worksite else COPY_POLICY
    copy_tools = COPY_PRESERVE_TOOLS if keep_worksite else COPY_TOOLS
    if scenario == "copy-page":
        execution_contract = get_scenario_spec(scenario).execution_contract
        page_tools = COPY_PAGE_PRESERVE_TOOLS if keep_worksite else COPY_PAGE_TOOLS
        anchor_keys = {
            "same-section": "semantic_page",
            "cross-section": "cross_section_anchor",
            "cross-notebook": "cross_notebook_anchor",
        }
        cases = []
        for index, case in enumerate(execution_contract.get("cases", []), start=1):
            declared_scope = case.get("include_descendants")
            destination_scope = str(case["destination_scope"])
            scope_label = {
                "same-section": "Same-Section",
                "cross-section": "Cross-Section",
                "cross-notebook": "Cross-Notebook",
            }.get(destination_scope)
            if scope_label is None:
                raise RunnerFailure(f"Unknown Copy Page destination scope: {destination_scope}")
            cases.append(
                {
                    "name": str(case["name"]),
                    "destination": resolve_manifest_item(
                        manifest, str(case["destination_key"])
                    ),
                    "destination_role": str(case["destination_role"]),
                    "destination_scope": destination_scope,
                    "collision_anchor": resolve_manifest_item(
                        manifest, anchor_keys[destination_scope]
                    ),
                    "destination_name": f"{index:02d}-{scope_label}-"
                    + ("Root-Only" if declared_scope == "omitted" else "Subtree")
                    + f"-Copy-{suffix}",
                    "include_descendants": (
                        None if declared_scope == "omitted" else bool(declared_scope)
                    ),
                    "expected_page_count": int(case["expected_page_count"]),
                }
            )
        if len(cases) != 6:
            raise RunnerFailure("Copy Page execution contract must declare exactly six cases.")
        return {
            "source": resolve_manifest_item(manifest, "parent_page"),
            "protected_page_ids": [
                str(resolve_manifest_item(manifest, key)["id"])
                for key in (
                    "parent_page",
                    "semantic_page",
                    "cross_section_anchor",
                    "cross_notebook_anchor",
                )
            ],
            "notebooks": dict(manifest.get("notebooks", {"source": manifest["notebook"]})),
            "tool": "copy_page",
            "cases": cases,
            "policy": copy_policy,
            "tools": page_tools,
        }
    if scenario in {"copy-section", "copy-section-group"}:
        execution_contract = get_scenario_spec(scenario).execution_contract
        notebooks = dict(manifest.get("notebooks", {"source": manifest["notebook"]}))
        if set(notebooks) != {"destination", "source"}:
            raise RunnerFailure(
                f"{scenario} requires exact source/destination Notebook roles."
            )
        cases = []
        for index, case in enumerate(execution_contract.get("cases", []), start=1):
            destination_key = str(case["destination_key"])
            if destination_key == "source_notebook":
                destination = notebooks["source"]
            elif destination_key == "destination_notebook":
                destination = notebooks["destination"]
            else:
                destination = resolve_manifest_item(manifest, destination_key)
            scope = str(case["destination_scope"])
            cases.append(
                {
                    "name": str(case["name"]),
                    "destination": destination,
                    "destination_role": str(case["destination_role"]),
                    "destination_scope": scope,
                    "destination_name": (
                        f"{index:02d}-"
                        + ("Same-Notebook" if scope == "same-notebook" else "Cross-Notebook")
                        + ("-Section-Copy-" if scenario == "copy-section" else "-Group-Copy-")
                        + suffix
                    ),
                }
            )
        if len(cases) != 2:
            raise RunnerFailure(f"{scenario} execution contract must declare exactly two cases.")
        return {
            "source": resolve_manifest_item(
                manifest,
                "source_section" if scenario == "copy-section" else "group_a",
            ),
            "notebooks": notebooks,
            "cases": cases,
            "tool": "copy_section" if scenario == "copy-section" else "copy_section_group",
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
) -> dict[str, Any]:
    common = {
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
            "expand_hierarchy",
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


async def close_copied_notebook(
    client: MCPStdioClient,
    target: dict[str, Any],
    evidence_path: Path,
) -> dict[str, Any]:
    """Refresh the exact copied Notebook before binding the close confirmation."""

    refreshed = await client.call_tool(
        "get_notebook",
        {"notebook_id": target["id"]},
    )
    close_target = refreshed.get("item")
    if (
        not isinstance(close_target, dict)
        or close_target.get("resource_type") != "notebook"
        or close_target.get("id") != target["id"]
        or display_name(close_target) != display_name(target)
    ):
        raise InvariantFailure(
            "Notebook Copy close confirmation did not refresh the exact target."
        )
    write_json(evidence_path, close_target)
    return await client.call_tool(
        "close_notebook",
        {
            "notebook_id": close_target["id"],
            "expected_name": display_name(close_target),
            "expected_modified": close_target.get("modified"),
        },
    )


async def _finalize_failed_copied_notebook(
    client: MCPStdioClient,
    result: Mapping[str, Any],
    planned: Mapping[str, Any],
    out: Path,
    *,
    keep_open: bool,
) -> dict[str, Any]:
    """Close a possibly-created Notebook Copy target before propagating failure."""

    evidence_path = out / "copy-target-failure-finalization.json"
    created_ids = [str(value) for value in result.get("created_ids", []) if value]
    target = result.get("item")
    possibly_created = bool(created_ids) or isinstance(target, Mapping)
    if not possibly_created:
        evidence = {
            "status": "not_created",
            "closed": True,
            "isolation_passed": True,
            "filesystem_deleted": False,
        }
        write_json(evidence_path, evidence)
        return evidence
    if keep_open:
        evidence = {
            "status": "preserved_open",
            "closed": False,
            "isolation_passed": False,
            "filesystem_deleted": False,
        }
        write_json(evidence_path, evidence)
        return evidence

    planned_path = str(planned.get("destination", {}).get("target_path", ""))
    actual_path = str(result.get("destination_path", ""))
    if (
        not isinstance(target, dict)
        or target.get("resource_type") != "notebook"
        or not target.get("id")
        or not planned_path
        or not actual_path
        or Path(actual_path).resolve() != Path(planned_path).resolve()
    ):
        evidence = {
            "status": "close_failed",
            "closed": False,
            "isolation_passed": False,
            "reason": "created Notebook target lacks an exact typed ID/path binding",
            "filesystem_deleted": False,
        }
        write_json(evidence_path, evidence)
        raise RestoreFailure(
            "Notebook Copy failure created a target without enough exact binding evidence to close it."
        )
    try:
        closed = await close_copied_notebook(
            client,
            target,
            out / "failure-close-confirmation.json",
        )
        if closed.get("closed") is not True:
            raise RestoreFailure("Notebook Copy failure close did not return closed=true.")
        evidence = {
            "status": "closed",
            "closed": True,
            "isolation_passed": True,
            "target_id": str(target["id"]),
            "target_path": actual_path,
            "close_result": closed,
            "filesystem_deleted": False,
        }
        write_json(evidence_path, evidence)
        return evidence
    except Exception as exc:
        evidence = {
            "status": "close_failed",
            "closed": False,
            "isolation_passed": False,
            "target_id": str(target["id"]),
            "target_path": actual_path,
            "error": f"{type(exc).__name__}: {exc}",
            "filesystem_deleted": False,
        }
        write_json(evidence_path, evidence)
        if isinstance(exc, RestoreFailure):
            raise
        raise RestoreFailure(
            f"Exact copied Notebook failure close failed: {exc}"
        ) from exc


async def _verify_and_finalize_notebook_copy(
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    before: dict[str, Any],
    current: dict[str, Any],
    copied: dict[str, Any],
    planned: dict[str, Any],
    spec: Mapping[str, Any],
    out: Path,
    *,
    keep_worksite: bool,
) -> dict[str, Any]:
    try:
        if copied.get("copy_report", {}).get("verified") is not True:
            raise InvariantFailure(
                "Copy response did not report successful read-back verification."
            )
        target = copied.get("item")
        if not isinstance(target, dict) or target.get("resource_type") != "notebook":
            raise InvariantFailure("Notebook Copy did not return a typed target Notebook.")
        actual_target_path = str(copied.get("destination_path", ""))
        planned_target_path = str(planned["destination"]["target_path"])
        if not actual_target_path or actual_target_path.casefold() != planned_target_path.casefold():
            raise InvariantFailure("Notebook Copy result path differs from the manifest-scoped plan.")
        target_snapshot = await capture_snapshot(client, target["id"])
        write_json(out / "after.json", target_snapshot)
        position_evidence = assert_destination_position(
            copied,
            target_snapshot,
            str(target["id"]),
        )
        write_json(out / "destination-position-evidence.json", position_evidence)
        assert_copy_mapping(
            before,
            target_snapshot,
            current["id"],
            None,
            str(spec["destination_name"]),
            copied,
            include_descendants=bool(spec.get("include_descendants", True)),
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
            closed = await close_copied_notebook(
                client,
                target,
                out / "close-confirmation.json",
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
    except Exception:
        await _finalize_failed_copied_notebook(
            client,
            copied,
            planned,
            out,
            keep_open=bool(
                getattr(args, "keep_notebook", False)
                or getattr(args, "keep_worksite", False)
            ),
        )
        raise


async def _capture_notebook_bundle(
    client: MCPStdioClient,
    notebooks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    role_order = tuple(sorted(notebooks))
    role_snapshots = {
        role: await capture_snapshot(client, str(notebooks[role]["id"]))
        for role in role_order
    }
    merged: dict[str, Any] = {
        "notebook_id": str(notebooks["source"]["id"]),
        "notebook_ids": {
            role: str(notebooks[role]["id"]) for role in role_order
        },
        "roles": role_snapshots,
        "items": [],
        "page_hashes": {},
        "page_canonical_hashes": {},
        "page_reparent_hashes": {},
        "page_xml_hashes": {},
        "page_objects": {},
        "page_capability_projections": {},
    }
    for role in role_order:
        snapshot = role_snapshots[role]
        merged["items"].extend(snapshot.get("items", []))
        for field in (
            "page_hashes",
            "page_canonical_hashes",
            "page_reparent_hashes",
            "page_xml_hashes",
            "page_objects",
            "page_capability_projections",
        ):
            value = snapshot.get(field, {})
            if isinstance(value, dict):
                merged[field].update(value)
    return merged


async def execute_copy_page(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    """Execute one source Page across three destinations and two subtree modes."""

    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    spec = copy_spec(
        "copy-page",
        manifest,
        options.run_dir,
        keep_worksite=keep_worksite,
        name_suffix=run_safe_timestamp(args),
    )
    cases = spec.get("cases")
    if not isinstance(cases, list) or len(cases) != 6:
        raise RunnerFailure("Copy Page requires its six declared execution cases.")
    notebooks = spec.get("notebooks")
    if not isinstance(notebooks, dict) or set(notebooks) != {"destination", "source"}:
        raise RunnerFailure("Copy Page requires exact source/destination Notebook roles.")
    protected_page_ids = spec.get("protected_page_ids")
    if not isinstance(protected_page_ids, list) or len(set(protected_page_ids)) != 4:
        raise RunnerFailure(
            "Copy Page requires exact source Parent/Child and two collision-anchor IDs."
        )

    out = scenario_dir(options.run_dir, "copy-page")
    async with scenario_client(
        client,
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as client:
        original_before = await _capture_notebook_bundle(client, notebooks)
        write_json(out / "before.json", original_before)
        current_snapshot = original_before
        copied_results: list[dict[str, Any]] = []
        case_results: list[dict[str, Any]] = []
        execution_index: list[dict[str, Any]] = []

        for case_index, case in enumerate(cases, start=1):
            case_name = str(case["name"])
            case_started = time.perf_counter()
            options.progress.unit_started("case", case_name, case_index, len(cases))
            include_descendants = case.get("include_descendants")
            effective_scope = include_descendants is True
            pre_plan_source = find_snapshot_item(current_snapshot, spec["source"]["id"])
            if pre_plan_source is None:
                raise RunnerFailure(
                    f"Manifest Copy source is not active before case '{case_name}'."
                )
            case_spec = {
                **spec,
                "destination": dict(case["destination"]),
                "destination_name": str(case["destination_name"]),
                "collision_anchor": dict(case["collision_anchor"]),
                "include_descendants": include_descendants,
            }
            case_before = current_snapshot
            write_json(out / f"before-{case_name}.json", case_before)
            collision_anchor = dict(case_spec["collision_anchor"])
            anchor_before = find_snapshot_item(
                case_before, str(collision_anchor["id"])
            )
            if anchor_before is None:
                raise InvariantFailure(
                    f"Copy case '{case_name}' is missing its same-title collision anchor."
                )
            current_source = dict(pre_plan_source)
            copied = await call_with_result_evidence(
                client,
                "copy_page",
                copy_execute_arguments(
                    case_spec,
                    current_source,
                ),
                out / f"copy-result-{case_name}.json",
            )
            report = copied.get("copy_report", {})
            planning = report.get("planning", {})
            if planning.get("include_descendants") is not effective_scope:
                raise InvariantFailure(
                    f"Page Copy internal planning scope differs for case '{case_name}'."
                )
            if effective_scope:
                assert_copy_fixture_capabilities(
                    planning,
                    {*RELAXED_COPY_CAPABILITIES, "DisplayEquation"},
                )
            else:
                assert_copy_fixture_capabilities(
                    planning,
                    {*ROOT_PAGE_COPY_CAPABILITIES, "DisplayEquation"},
                    include_automated_defaults=False,
                )
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

            case_after = await _capture_notebook_bundle(client, notebooks)
            write_json(out / f"after-{case_name}.json", case_after)
            assert_copy_mapping(
                case_before,
                case_after,
                current_source["id"],
                case_spec["destination"]["id"],
                case_spec["destination_name"],
                copied,
                include_descendants=effective_scope,
            )
            position_evidence = assert_destination_position(
                copied,
                case_after,
                str(target["id"]),
            )
            write_json(
                out / f"destination-position-evidence-{case_name}.json",
                position_evidence,
            )
            anchor_after = find_snapshot_item(
                case_after, str(collision_anchor["id"])
            )
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
            if anchor_after is None or any(
                anchor_after.get(field) != anchor_before.get(field)
                for field in stable_fields
            ):
                raise InvariantFailure(
                    f"Copy case '{case_name}' changed or reordered its collision anchor."
                )
            if str(collision_anchor["id"]) in {
                str(value) for value in id_map.values()
            }:
                raise InvariantFailure(
                    f"Copy case '{case_name}' reused its collision anchor as a target."
                )
            assert_pages_unchanged(
                case_before,
                case_after,
                protected_page_ids,
            )
            copied_results.append(copied)
            case_result = {
                "case": case_name,
                "parameter": (
                    "omitted" if include_descendants is None else include_descendants
                ),
                "effective_include_descendants": effective_scope,
                "destination_name": case_spec["destination_name"],
                "destination_role": case["destination_role"],
                "destination_scope": case["destination_scope"],
                "destination_section_id": case_spec["destination"]["id"],
                "target_id": copied.get("item", {}).get("id"),
                "mapped_page_count": len(id_map),
                "collision_anchor_id": collision_anchor["id"],
                "collision_anchor_unchanged": True,
                "copy_report": report,
                "destination_position": position_evidence,
            }
            case_results.append(case_result)
            execution_index.append(
                {
                    "case": case_name,
                    "parameter": case_result["parameter"],
                    "effective_include_descendants": effective_scope,
                    "planning": planning,
                }
            )
            current_snapshot = case_after
            options.progress.unit_completed(
                "case",
                case_name,
                case_index,
                len(cases),
                elapsed_seconds=time.perf_counter() - case_started,
            )

        write_json(out / "internal-planning.json", {"cases": execution_index})
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
                    "--keep-worksite preserved all six verified Page Copy targets."
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
        restored = await _capture_notebook_bundle(client, notebooks)
        write_json(out / "restored.json", restored)
        assert_copy_page_restored(
            original_before,
            restored,
            protected_page_ids,
        )
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


async def execute_copy_display_equation(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    """Run a fixed three-hop DisplayEquation Copy chain without human input."""

    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    source_id = str(resolve_manifest_item(manifest, "canvas_page")["id"])
    section_id = str(resolve_manifest_item(manifest, "canvas_section")["id"])
    out = scenario_dir(options.run_dir, "copy-display-equation")
    policy = COPY_NO_DELETE_POLICY if keep_worksite else COPY_POLICY
    tools = COPY_PAGE_PRESERVE_TOOLS if keep_worksite else COPY_PAGE_TOOLS

    async with scenario_client(
        client,
        policy=policy,
        allowed_tools=tools,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as active_client:
        original_before = await capture_snapshot(active_client, notebook_id)
        write_json(out / "before.json", original_before)
        current_snapshot = original_before
        current_source_id = source_id
        copied_results: list[dict[str, Any]] = []
        hops: list[dict[str, Any]] = []

        for hop in range(1, 4):
            hop_started = time.perf_counter()
            options.progress.unit_started("hop", f"display-equation-{hop}", hop, 3)
            current_source = find_snapshot_item(current_snapshot, current_source_id)
            if current_source is None or current_source.get("resource_type") != "page":
                raise InvariantFailure(
                    f"DisplayEquation chain source is missing before hop {hop}."
                )
            destination_title = f"{hop + 1:02d}-DisplayEquation-Copy-Hop-{hop}-" + run_safe_timestamp(args)
            protected_page_ids = [
                str(item["id"])
                for item in current_snapshot.get("items", ())
                if isinstance(item, dict)
                and item.get("resource_type") == "page"
                and item.get("id")
            ]
            copied = await call_with_result_evidence(
                active_client,
                "copy_page",
                copy_execute_arguments(
                    {
                        "tool": "copy_page",
                        "destination": {"id": section_id},
                        "destination_name": destination_title,
                        "include_descendants": False,
                    },
                    dict(current_source),
                ),
                out / f"copy-result-hop-{hop}.json",
            )
            report = copied.get("copy_report", {})
            assert_copy_fixture_capabilities(
                report.get("planning", {}),
                {*ROOT_PAGE_COPY_CAPABILITIES, "DisplayEquation"},
                include_automated_defaults=False,
            )
            page_results = report.get("page_results", ())
            page_result = page_results[0] if len(page_results) == 1 else {}
            equivalence = (
                page_result.get("equivalence", {})
                if isinstance(page_result, dict)
                else {}
            )
            display_comparison = equivalence.get(
                "display_equation_comparison", {}
            )
            normalizations = (
                page_result.get("normalizations", {})
                if isinstance(page_result, dict)
                else {}
            )
            if not (
                report.get("verified") is True
                and report.get("lossless") is True
                and report.get("copy_contract_satisfied") is True
                and equivalence.get("equivalent") is True
                and equivalence.get("verification_tier")
                == "semantic_display_equation"
                and display_comparison.get("passed") is True
                and int(
                    normalizations.get(
                        "display_equation_empty_spans_removed", 0
                    )
                )
                == 1
                and int(
                    normalizations.get(
                        "redundant_breaks_before_display_mathml_removed", 0
                    )
                )
                == 1
            ):
                raise InvariantFailure(
                    f"DisplayEquation production Copy gate failed at hop {hop}."
                )

            target = copied.get("item", {})
            target_id = str(target.get("id", ""))
            if not target_id:
                raise InvariantFailure(
                    f"DisplayEquation Copy returned no exact target at hop {hop}."
                )
            after = await capture_snapshot(active_client, notebook_id)
            write_json(out / f"after-hop-{hop}.json", after)
            assert_copy_mapping(
                current_snapshot,
                after,
                current_source_id,
                section_id,
                destination_title,
                copied,
                include_descendants=False,
            )
            assert_pages_unchanged(
                current_snapshot,
                after,
                protected_page_ids,
            )
            target_projection = after.get("page_capability_projections", {}).get(
                target_id, {}
            )
            if not (
                isinstance(target_projection, dict)
                and target_projection.get("complete") is True
                and "DisplayEquation"
                in set(target_projection.get("capabilities", ()))
            ):
                raise InvariantFailure(
                    f"DisplayEquation target detector failed at hop {hop}."
                )
            target_xml_result = await active_client.call_tool(
                "get_page_xml",
                {"page_id": target_id, "page_info": "all"},
            )
            target_structure = mathml_structure_projection(
                str(target_xml_result["xml"])
            )
            candidates = target_structure.get("candidates", ())
            candidate = candidates[0] if len(candidates) == 1 else {}
            target_break_count = int(candidate.get("oe_direct_t_break_count", -1))
            if not (
                target_structure.get("complete") is True
                and target_structure.get("display_attribute_equation_count") == 1
                and target_structure.get("standalone_candidate_count") == 1
                and target_break_count == 1
                and candidate.get("known_onenote_display_break_wrapper") is True
            ):
                raise InvariantFailure(
                    f"DisplayEquation target wrapper is not bounded at hop {hop}."
                )
            hops.append(
                {
                    "hop": hop,
                    "source_id": current_source_id,
                    "target_id": target_id,
                    "verification_tier": equivalence.get("verification_tier"),
                    "verified": report.get("verified"),
                    "lossless": report.get("lossless"),
                    "copy_contract_satisfied": report.get(
                        "copy_contract_satisfied"
                    ),
                    "removed_empty_spans": normalizations.get(
                        "display_equation_empty_spans_removed"
                    ),
                    "removed_breaks": normalizations.get(
                        "redundant_breaks_before_display_mathml_removed"
                    ),
                    "target_break_count": target_break_count,
                }
            )
            copied_results.append(copied)
            current_source_id = target_id
            current_snapshot = after
            options.progress.unit_completed(
                "hop",
                f"display-equation-{hop}",
                hop,
                3,
                elapsed_seconds=time.perf_counter() - hop_started,
            )

        write_json(out / "copy-chain.json", {"schema_version": 2, "hops": hops})
        target_ids = [str(value["target_id"]) for value in hops]
        if keep_worksite:
            remaining = {
                "status": "preserved_active_for_manual_inspection",
                "target_ids": target_ids,
                "manual_cleanup_required": True,
            }
            write_json(out / "worksite.json", remaining)
            result = {
                "scenario": "copy-display-equation",
                "status": "passed",
                "target_ids": target_ids,
                "hops": hops,
                "restored": False,
                "worksite_preserved": True,
                "cleanup_deleted_ids": [],
                "remaining_state": remaining,
            }
            write_json(out / "result.json", result)
            render_report(options.run_dir)
            return result

        deleted_ids: list[str] = []
        for copied in reversed(copied_results):
            deleted_ids.extend(await cleanup_copy(active_client, current_snapshot, copied))
        restored = await capture_snapshot(active_client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_copy_page_restored(original_before, restored, [source_id])
        result = {
            "scenario": "copy-display-equation",
            "status": "passed",
            "target_ids": target_ids,
            "hops": hops,
            "restored": True,
            "worksite_preserved": False,
            "cleanup_deleted_ids": deleted_ids,
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result


async def execute_copy_container(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    client: MCPStdioClient | None = None,
) -> dict[str, Any]:
    """Copy one Section or SectionGroup inside and across a two-Notebook bundle."""

    validate_manifest_notebook(manifest, args.notebook_name)
    keep_worksite = bool(getattr(args, "keep_worksite", False))
    spec = copy_spec(
        args.scenario,
        manifest,
        options.run_dir,
        keep_worksite=keep_worksite,
        name_suffix=run_safe_timestamp(args),
    )
    cases = spec.get("cases")
    notebooks = spec.get("notebooks")
    if not isinstance(cases, list) or len(cases) != 2:
        raise RunnerFailure(f"{args.scenario} requires two declared execution cases.")
    if not isinstance(notebooks, dict) or set(notebooks) != {"destination", "source"}:
        raise RunnerFailure(
            f"{args.scenario} requires exact source/destination Notebook roles."
        )

    out = scenario_dir(options.run_dir, args.scenario)
    async with scenario_client(
        client,
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as active_client:
        original_before = await _capture_notebook_bundle(active_client, notebooks)
        write_json(out / "before.json", original_before)
        current_snapshot = original_before
        copied_results: list[dict[str, Any]] = []
        case_results: list[dict[str, Any]] = []
        execution_index: list[dict[str, Any]] = []

        for case_index, case in enumerate(cases, start=1):
            case_name = str(case["name"])
            case_started = time.perf_counter()
            options.progress.unit_started("case", case_name, case_index, len(cases))
            pre_plan_source = find_snapshot_item(current_snapshot, spec["source"]["id"])
            if pre_plan_source is None:
                raise RunnerFailure(
                    f"Manifest Copy source is not active before case '{case_name}'."
                )
            case_spec = {
                **spec,
                "destination": dict(case["destination"]),
                "destination_name": str(case["destination_name"]),
            }
            case_before = current_snapshot
            write_json(out / f"before-{case_name}.json", case_before)
            current_source = dict(pre_plan_source)
            copied = await call_with_result_evidence(
                active_client,
                spec["tool"],
                copy_execute_arguments(
                    case_spec,
                    current_source,
                ),
                out / f"copy-result-{case_name}.json",
            )
            report = copied.get("copy_report", {})
            planning = report.get("planning", {})
            assert_copy_fixture_capabilities(planning, RELAXED_COPY_CAPABILITIES)
            target = copied.get("item")
            expected_type = (
                "section" if spec["tool"] == "copy_section" else "section_group"
            )
            if report.get("verified") is not True:
                raise InvariantFailure(
                    f"Copy case '{case_name}' did not report verified read-back."
                )
            if not isinstance(target, dict) or target.get("resource_type") != expected_type:
                raise InvariantFailure(
                    f"Copy case '{case_name}' did not return a typed {expected_type} root."
                )

            case_after = await _capture_notebook_bundle(active_client, notebooks)
            write_json(out / f"after-{case_name}.json", case_after)
            assert_copy_mapping(
                case_before,
                case_after,
                current_source["id"],
                case_spec["destination"]["id"],
                case_spec["destination_name"],
                copied,
            )
            position_evidence = assert_destination_position(
                copied,
                case_after,
                str(target["id"]),
            )
            write_json(
                out / f"destination-position-evidence-{case_name}.json",
                position_evidence,
            )
            protected_pages = [
                str(item["id"])
                for item in case_before.get("items", [])
                if item.get("resource_type") == "page" and item.get("id")
            ]
            assert_pages_unchanged(case_before, case_after, protected_pages)
            copied_results.append(copied)
            case_results.append(
                {
                    "case": case_name,
                    "destination_name": case_spec["destination_name"],
                    "destination_role": case["destination_role"],
                    "destination_scope": case["destination_scope"],
                    "destination_parent_id": case_spec["destination"]["id"],
                    "target_id": target["id"],
                    "mapped_resource_count": len(report.get("id_map", {})),
                    "copy_report": report,
                    "destination_position": position_evidence,
                }
            )
            execution_index.append(
                {
                    "case": case_name,
                    "planning": planning,
                }
            )
            current_snapshot = case_after
            options.progress.unit_completed(
                "case",
                case_name,
                case_index,
                len(cases),
                elapsed_seconds=time.perf_counter() - case_started,
            )

        write_json(out / "internal-planning.json", {"cases": execution_index})
        write_json(out / "after.json", current_snapshot)
        target_ids = [
            str(target_id)
            for copied in copied_results
            for target_id in copied.get("copy_report", {}).get("id_map", {}).values()
        ]
        if keep_worksite:
            remaining = {
                "status": "preserved_active_for_manual_inspection",
                "target_ids": target_ids,
                "target_root_ids": [
                    str(copied.get("item", {}).get("id")) for copied in copied_results
                ],
                "cases": case_results,
                "manual_cleanup_required": True,
                "reason": (
                    f"--keep-worksite preserved both verified {args.scenario} targets."
                ),
            }
            write_json(out / "worksite.json", remaining)
            result = {
                "scenario": args.scenario,
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
            deleted_ids.extend(await cleanup_copy(active_client, current_snapshot, copied))
        restored = await _capture_notebook_bundle(active_client, notebooks)
        write_json(out / "restored.json", restored)
        assert_restored(original_before, restored)
        result = {
            "scenario": args.scenario,
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
    if args.scenario in {"copy-section", "copy-section-group"}:
        return await execute_copy_container(
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
        name_suffix=run_safe_timestamp(args),
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
        current = dict(pre_plan_source)
        operation_context = {
            "destination": {
                "target_path": (
                    str(
                        Path(spec["destination_base_folder"])
                        / spec["destination_name"]
                    )
                    if spec.get("destination_base_folder")
                    else ""
                )
            }
        }
        try:
            copied = await call_with_result_evidence(
                client,
                spec["tool"],
                copy_execute_arguments(spec, current),
                out / "copy-result.json",
            )
        except Exception:
            if args.scenario == "copy-notebook":
                partial = (
                    read_json(out / "copy-result.json")
                    if (out / "copy-result.json").is_file()
                    else {}
                )
                await _finalize_failed_copied_notebook(
                    client,
                    partial,
                    operation_context,
                    out,
                    keep_open=bool(
                        getattr(args, "keep_notebook", False)
                        or getattr(args, "keep_worksite", False)
                    ),
                )
            raise

        if args.scenario == "copy-notebook":
            return await _verify_and_finalize_notebook_copy(
                args,
                options,
                client,
                before,
                current,
                copied,
                operation_context,
                spec,
                out,
                keep_worksite=keep_worksite,
            )

        report = copied.get("copy_report", {})
        planning = report.get("planning", {})
        if spec["tool"] == "copy_page":
            if planning.get("include_descendants") is not spec["include_descendants"]:
                raise InvariantFailure(
                    "Page Copy internal planning scope differs from the scenario's fixed scope."
                )
            assert_copy_fixture_capabilities(
                planning,
                ROOT_PAGE_COPY_CAPABILITIES,
                include_automated_defaults=False,
            )
        else:
            assert_copy_fixture_capabilities(planning, RELAXED_COPY_CAPABILITIES)
        if report.get("verified") is not True:
            raise InvariantFailure("Copy response did not report successful read-back verification.")

        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        target_id = str(copied.get("item", {}).get("id", ""))
        position_evidence = assert_destination_position(copied, after, target_id)
        write_json(out / "destination-position-evidence.json", position_evidence)
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
    "close_copied_notebook",
    "copy_execute_arguments",
    "copy_spec",
    "execute_copy",
    "execute_copy_container",
    "execute_copy_page",
]
