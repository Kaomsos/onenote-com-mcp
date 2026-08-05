"""Page, section, section-group, and notebook copy scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    ClientFailure,
    MCPStdioClient,
)
from ..runner import (
    InvariantFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
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
from ._config import COPY_NOTEBOOK_TOOLS, COPY_TOOLS
from .copy_invariants import assert_copy_fixture_capabilities, assert_copy_mapping
from .report import render_report


def copy_spec(scenario: str, manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    suffix = timestamp()
    if scenario == "copy-page":
        return {
            "source": resolve_manifest_item(manifest, "parent_page"),
            "destination": resolve_manifest_item(manifest, "disposable_section"),
            "destination_name": f"Copy-Parent-{suffix}",
            "tool": "copy_page",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
        }
    if scenario == "copy-section":
        return {
            "source": resolve_manifest_item(manifest, "move_source"),
            "destination": resolve_manifest_item(manifest, "group_b"),
            "destination_name": f"Copy-Section-{suffix}",
            "tool": "copy_section",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
        }
    if scenario == "copy-section-group":
        return {
            "source": resolve_manifest_item(manifest, "group_a"),
            "destination": manifest["notebook"],
            "destination_name": f"Copy-Group-{suffix}",
            "tool": "copy_section_group",
            "policy": COPY_POLICY,
            "tools": COPY_TOOLS,
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
            "destination_name": f"{display_name(manifest['notebook'])}-Copy-{suffix}",
            "destination_base_folder": str(expected_root),
            "tool": "copy_notebook",
            "policy": COPY_NO_DELETE_POLICY,
            "tools": COPY_NOTEBOOK_TOOLS,
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
        return {
            **common,
            "page_id": source["id"],
            "destination_section_id": spec["destination"]["id"],
            "expected_title": display_name(source),
            "expected_section_id": source["section_id"],
            "destination_title": spec["destination_name"],
        }
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


async def run_copy(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    spec = copy_spec(args.scenario, manifest, options.run_dir)
    if args.scenario == "copy-notebook":
        Path(spec["destination_base_folder"]).mkdir(parents=True, exist_ok=True)
    out = scenario_dir(options.run_dir, args.scenario)
    async with MCPStdioClient(
        policy=spec["policy"],
        allowed_tools=spec["tools"],
        run_dir=out,
        timeout_seconds=options.timeout,
    ) as client:
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        current = find_snapshot_item(before, spec["source"]["id"])
        if current is None:
            raise RunnerFailure("Manifest Copy source is not active in the current snapshot.")
        plan_arguments = {
            "source_id": current["id"],
            "destination_name": spec["destination_name"],
        }
        if spec["destination"] is not None:
            plan_arguments["destination_parent_id"] = spec["destination"]["id"]
        if spec.get("destination_base_folder"):
            plan_arguments["destination_base_folder"] = spec["destination_base_folder"]
        planned = await client.call_tool("plan_copy", plan_arguments)
        write_json(out / "plan.json", planned)
        assert_copy_fixture_capabilities(planned)
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
            )
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
        )
        for source_id, source_hash in before["page_hashes"].items():
            if after["page_hashes"].get(source_id) != source_hash:
                raise InvariantFailure("Copy changed an existing source Page XML hash.")
        deleted_ids = await cleanup_copy(client, after, copied)
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(before, restored)
        result = {
            "scenario": args.scenario,
            "status": "passed",
            "target_id": copied.get("item", {}).get("id"),
            "restored": True,
            "cleanup_deleted_ids": deleted_ids,
            "copy_report": copied["copy_report"],
        }
        write_json(out / "result.json", result)
        render_report(options.run_dir)
        return result
