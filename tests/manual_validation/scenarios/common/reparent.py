"""Shared raw-XML capability probes for ID-preserving reparent operations."""

from __future__ import annotations

import argparse
import json
from typing import Any
import xml.etree.ElementTree as ET

from local_onenote_mcp.constants import ONE_NS

from ...mcp_stdio_client import (
    MCPStdioClient,
    REPARENT_PROBE_POLICY,
    scenario_client,
)
from ...runtime import InvariantFailure, RestoreFailure, RuntimeOptions
from ...test_utils import (
    assert_restored,
    capture_snapshot,
    display_name,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)


ET.register_namespace("one", ONE_NS)


def build_reparent_xml(
    notebook: dict[str, Any],
    destination_parent: dict[str, Any],
    target: dict[str, Any],
    resource_type: str,
) -> str:
    """Build the smallest unambiguous hierarchy update for a disposable target."""

    root = ET.Element(f"{{{ONE_NS}}}Notebooks")
    notebook_node = ET.SubElement(
        root,
        f"{{{ONE_NS}}}Notebook",
        {"ID": str(notebook["id"]), "name": display_name(notebook)},
    )
    if resource_type == "page":
        if destination_parent.get("resource_type") != "section":
            raise ValueError("Page reparent destination must be a Section.")
        parent_node = ET.SubElement(
            notebook_node,
            f"{{{ONE_NS}}}Section",
            {
                "ID": str(destination_parent["id"]),
                "name": display_name(destination_parent),
            },
        )
        ET.SubElement(
            parent_node,
            f"{{{ONE_NS}}}Page",
            {
                "ID": str(target["id"]),
                "name": display_name(target),
                "pageLevel": str(max(1, int(target.get("page_level", 1)))),
            },
        )
    elif resource_type == "section_group":
        destination_type = destination_parent.get("resource_type")
        if destination_type == "notebook":
            parent_node = notebook_node
        elif destination_type == "section_group":
            parent_node = ET.SubElement(
                notebook_node,
                f"{{{ONE_NS}}}SectionGroup",
                {
                    "ID": str(destination_parent["id"]),
                    "name": display_name(destination_parent),
                },
            )
        else:
            raise ValueError(
                "SectionGroup reparent destination must be a Notebook or SectionGroup."
            )
        ET.SubElement(
            parent_node,
            f"{{{ONE_NS}}}SectionGroup",
            {"ID": str(target["id"]), "name": display_name(target)},
        )
    else:
        raise ValueError(f"Unsupported reparent resource type: {resource_type}")
    return ET.tostring(root, encoding="unicode")


def _container_parent(item: dict[str, Any], resource_type: str) -> Any:
    return item.get("section_id") if resource_type == "page" else item.get("parent_id")


def _validate_identity_preserving_reparented_snapshot(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str,
    destination_parent_id: str,
    resource_type: str,
) -> dict[str, bool]:
    if snapshot_ids(before) != snapshot_ids(after):
        raise InvariantFailure("Reparent changed one or more hierarchy object IDs.")
    before_by_id = {str(item["id"]): item for item in before.get("items", [])}
    after_by_id = {str(item["id"]): item for item in after.get("items", [])}
    before_target = before_by_id.get(target_id)
    after_target = after_by_id.get(target_id)
    if before_target is None or after_target is None:
        raise InvariantFailure("Reparent target disappeared during read-back.")
    if after_target.get("resource_type") != resource_type:
        raise InvariantFailure("Reparent target type changed during read-back.")
    if _container_parent(after_target, resource_type) != destination_parent_id:
        raise InvariantFailure("UpdateHierarchy returned without applying the requested parent.")
    if after_target.get("notebook_id") != before_target.get("notebook_id"):
        raise InvariantFailure("Same-Notebook reparent changed the target notebook identity.")
    if display_name(after_target) != display_name(before_target):
        raise InvariantFailure("Reparent changed the target display name.")
    if resource_type == "page":
        if after_target.get("parent_id") != destination_parent_id:
            raise InvariantFailure("Page parent_id and section_id disagree after reparent.")
        if (
            after_target.get("page_level") != before_target.get("page_level")
            or after_target.get("parent_page_id") != before_target.get("parent_page_id")
        ):
            raise InvariantFailure("Page reparent changed indentation topology.")

    relationship_fields = (
        "parent_id",
        "section_id",
        "page_level",
        "order",
        "parent_page_id",
    )
    for object_id, before_item in before_by_id.items():
        after_item = after_by_id[object_id]
        if after_item.get("resource_type") != before_item.get("resource_type"):
            raise InvariantFailure(f"Object type changed during reparent: {object_id}")
        if display_name(after_item) != display_name(before_item):
            raise InvariantFailure(f"Object name/title changed during reparent: {object_id}")
        if after_item.get("notebook_id") != before_item.get("notebook_id"):
            raise InvariantFailure(f"Object escaped the source Notebook: {object_id}")
        if object_id == target_id:
            continue
        if any(after_item.get(field) != before_item.get(field) for field in relationship_fields):
            raise InvariantFailure(f"Unrelated hierarchy relationship changed: {object_id}")

    if before.get("page_hashes") != after.get("page_hashes"):
        raise InvariantFailure("Reparent changed one or more stable Page content hashes.")
    if before.get("page_objects") != after.get("page_objects"):
        raise InvariantFailure("Reparent changed one or more Page content-object identities.")
    return {
        "target_id_preserved": True,
        "hierarchy_ids_preserved": True,
        "same_notebook_preserved": True,
        "unrelated_relationships_preserved": True,
        "page_content_preserved": True,
        "page_object_ids_preserved": True,
    }


_PAGE_OBJECT_IDENTITY_FIELDS = {
    "id",
    "object_id",
    "callback_id",
    "delete_target_id",
    "delete_object_id",
    "container_object_id",
    "parent_object_id",
    "page_id",
}


def _page_object_semantics(snapshot: dict[str, Any], page_id: str) -> list[dict[str, Any]]:
    projected = [
        {
            key: value
            for key, value in item.items()
            if key not in _PAGE_OBJECT_IDENTITY_FIELDS
        }
        for item in snapshot.get("page_objects", {}).get(page_id, [])
        if isinstance(item, dict)
    ]
    return sorted(
        projected,
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )


def _locate_reparented_page_id(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str,
    destination_parent_id: str,
) -> str:
    """Resolve the one Page identity retained or remapped by native UpdateHierarchy."""

    before_ids = snapshot_ids(before)
    after_ids = snapshot_ids(after)
    if target_id in after_ids:
        if before_ids != after_ids:
            raise InvariantFailure(
                "Page reparent preserved the target ID but changed other hierarchy object IDs."
            )
        return target_id

    removed_ids = before_ids - after_ids
    added_ids = after_ids - before_ids
    if removed_ids != {target_id} or len(added_ids) != 1:
        raise InvariantFailure(
            "Page reparent did not produce one exact old-ID to new-ID transition."
        )
    replacement_id = next(iter(added_ids))
    replacement = find_snapshot_item(after, replacement_id)
    if (
        replacement is None
        or replacement.get("resource_type") != "page"
        or _container_parent(replacement, "page") != destination_parent_id
    ):
        raise InvariantFailure(
            "Page reparent replacement is not the one Page added to the destination Section."
        )
    return replacement_id


def _validate_page_reparented_snapshot(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str,
    destination_parent_id: str,
) -> tuple[str, dict[str, bool]]:
    """Validate native Page reparent while allowing a one-to-one ID remap."""

    after_target_id = _locate_reparented_page_id(
        before,
        after,
        target_id=target_id,
        destination_parent_id=destination_parent_id,
    )
    before_by_id = {str(item["id"]): item for item in before.get("items", [])}
    after_by_id = {str(item["id"]): item for item in after.get("items", [])}
    before_target = before_by_id.get(target_id)
    after_target = after_by_id.get(after_target_id)
    if before_target is None or after_target is None:
        raise InvariantFailure("Page reparent target disappeared during read-back.")
    if _container_parent(after_target, "page") != destination_parent_id:
        raise InvariantFailure("UpdateHierarchy returned without applying the requested parent.")
    if after_target.get("parent_id") != destination_parent_id:
        raise InvariantFailure("Page parent_id and section_id disagree after reparent.")
    if after_target.get("notebook_id") != before_target.get("notebook_id"):
        raise InvariantFailure("Same-Notebook Page reparent changed the Notebook identity.")
    if display_name(after_target) != display_name(before_target):
        raise InvariantFailure("Page reparent changed the target title.")
    if (
        after_target.get("page_level") != before_target.get("page_level")
        or after_target.get("parent_page_id") != before_target.get("parent_page_id")
    ):
        raise InvariantFailure("Page reparent changed indentation topology.")

    relationship_fields = (
        "parent_id",
        "section_id",
        "page_level",
        "order",
        "parent_page_id",
    )
    for object_id, before_item in before_by_id.items():
        if object_id == target_id:
            continue
        after_item = after_by_id.get(object_id)
        if after_item is None:
            raise InvariantFailure(f"Unrelated hierarchy object disappeared: {object_id}")
        if after_item.get("resource_type") != before_item.get("resource_type"):
            raise InvariantFailure(f"Object type changed during Page reparent: {object_id}")
        if display_name(after_item) != display_name(before_item):
            raise InvariantFailure(f"Object name/title changed during Page reparent: {object_id}")
        if after_item.get("notebook_id") != before_item.get("notebook_id"):
            raise InvariantFailure(f"Object escaped the source Notebook: {object_id}")
        if any(after_item.get(field) != before_item.get(field) for field in relationship_fields):
            raise InvariantFailure(f"Unrelated hierarchy relationship changed: {object_id}")

    before_reparent_hashes = before.get("page_reparent_hashes", {})
    after_reparent_hashes = after.get("page_reparent_hashes", {})
    if not before_reparent_hashes.get(target_id) or not after_reparent_hashes.get(
        after_target_id
    ):
        raise InvariantFailure("Page reparent snapshot is missing rich semantic content evidence.")
    if before_reparent_hashes[target_id] != after_reparent_hashes[after_target_id]:
        raise InvariantFailure(
            "Page reparent changed rich content after allowing regenerated IDs and Tag indices."
        )
    for page_id, digest in before_reparent_hashes.items():
        if page_id != target_id and after_reparent_hashes.get(page_id) != digest:
            raise InvariantFailure(f"Unrelated Page rich semantic content changed: {page_id}")

    before_hashes = before.get("page_hashes", {})
    after_hashes = after.get("page_hashes", {})
    for page_id, digest in before_hashes.items():
        if page_id != target_id and after_hashes.get(page_id) != digest:
            raise InvariantFailure(f"Unrelated stable Page content changed: {page_id}")
    before_objects = before.get("page_objects", {})
    after_objects = after.get("page_objects", {})
    for page_id, objects in before_objects.items():
        if page_id != target_id and after_objects.get(page_id) != objects:
            raise InvariantFailure(f"Unrelated Page content-object identities changed: {page_id}")
    if _page_object_semantics(before, target_id) != _page_object_semantics(
        after, after_target_id
    ):
        raise InvariantFailure("Page reparent changed the target content-object semantics.")

    return after_target_id, {
        "target_id_transition_valid": True,
        "same_notebook_preserved": True,
        "unrelated_relationships_preserved": True,
        "rich_semantic_content_preserved": True,
        "content_object_semantics_preserved": True,
        "unrelated_page_content_preserved": True,
    }


def _validate_reparented_snapshot(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str,
    destination_parent_id: str,
    resource_type: str,
) -> tuple[str, dict[str, bool]]:
    if resource_type == "page":
        return _validate_page_reparented_snapshot(
            before,
            after,
            target_id=target_id,
            destination_parent_id=destination_parent_id,
        )
    return target_id, _validate_identity_preserving_reparented_snapshot(
        before,
        after,
        target_id=target_id,
        destination_parent_id=destination_parent_id,
        resource_type=resource_type,
    )


async def execute_reparent_probe(
    *,
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    scenario_name: str,
    resource_type: str,
    target_key: str | None,
    source_parent_key: str | None,
    destination_parent_key: str | None,
    allowed_tools: set[str],
    client: MCPStdioClient | None,
    plans: tuple[tuple[str, str, str | None, str | None], ...] | None = None,
) -> dict[str, Any]:
    notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
    notebook = manifest["notebook"]
    if plans is None:
        if target_key is None:
            raise ValueError("A target key is required for a single reparent probe.")
        plans = ((scenario_name, target_key, source_parent_key, destination_parent_key),)
    out = scenario_dir(options.run_dir, scenario_name)

    def parent(key: str | None) -> dict[str, Any]:
        return notebook if key is None else resolve_manifest_item(manifest, key)

    async def restore_operations(
        active_client: MCPStdioClient,
        current_snapshot: dict[str, Any],
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        restore_snapshot = current_snapshot
        for index, operation in enumerate(reversed(operations), start=1):
            active_target_id = operation.get("current_target_id", operation["target_id"])
            restore_target = find_snapshot_item(
                restore_snapshot, active_target_id
            )
            if restore_target is None:
                raise RestoreFailure(
                    f"{operation['case']} target disappeared before restoration."
                )
            observed_parent = _container_parent(restore_target, resource_type)
            if observed_parent == operation["source_parent_id"]:
                continue
            if observed_parent != operation["destination_parent_id"]:
                raise RestoreFailure(
                    f"{operation['case']} target has an unknown parent; refusing recovery."
                )
            restore_parent = find_snapshot_item(
                restore_snapshot, operation["source_parent_id"]
            )
            if restore_parent is None:
                raise RestoreFailure(
                    f"{operation['case']} source parent disappeared before restoration."
                )
            restore_xml = build_reparent_xml(
                notebook,
                restore_parent,
                restore_target,
                resource_type,
            )
            operation["restore_xml"] = restore_xml
            step_before = restore_snapshot
            await active_client.call_tool(
                "update_hierarchy_xml", {"xml": restore_xml}
            )
            restore_snapshot = await capture_snapshot(active_client, notebook_id)
            write_json(out / f"restore-{index}.json", restore_snapshot)
            restored_target_id, _restore_checks = _validate_reparented_snapshot(
                step_before,
                restore_snapshot,
                target_id=active_target_id,
                destination_parent_id=operation["source_parent_id"],
                resource_type=resource_type,
            )
            operation["current_target_id"] = restored_target_id
            operation.setdefault("restore_id_maps", []).append(
                {active_target_id: restored_target_id}
            )
            if restored_target_id not in operation["id_history"]:
                operation["id_history"].append(restored_target_id)
        write_json(out / "restored.json", restore_snapshot)
        if resource_type == "page":
            operation = operations[0]
            _validate_page_reparented_snapshot(
                before,
                restore_snapshot,
                target_id=operation["target_id"],
                destination_parent_id=operation["source_parent_id"],
            )
        else:
            assert_restored(before, restore_snapshot)
        return restore_snapshot

    async with scenario_client(
        client,
        policy=REPARENT_PROBE_POLICY,
        allowed_tools=allowed_tools,
        run_dir=out,
        timeout_seconds=options.timeout,
        client_factory=MCPStdioClient,
    ) as active_client:
        before = await capture_snapshot(active_client, notebook_id)
        write_json(out / "before.json", before)
        current_snapshot = before
        operations: list[dict[str, Any]] = []
        verified: dict[str, dict[str, bool]] = {}

        for index, (case, plan_target_key, source_key, destination_key) in enumerate(
            plans, start=1
        ):
            declared_target = resolve_manifest_item(manifest, plan_target_key)
            source = parent(source_key)
            destination = parent(destination_key)
            current = find_snapshot_item(current_snapshot, str(declared_target["id"]))
            if current is None or _container_parent(current, resource_type) != source["id"]:
                raise InvariantFailure(
                    f"{case} target is not under its manifest-bound source parent."
                )
            operation = {
                "case": case,
                "target_id": str(current["id"]),
                "current_target_id": str(current["id"]),
                "id_history": [str(current["id"])],
                "source_parent_id": str(source["id"]),
                "destination_parent_id": str(destination["id"]),
                "forward_xml": build_reparent_xml(
                    notebook, destination, current, resource_type
                ),
                "restore_xml": build_reparent_xml(
                    notebook, source, current, resource_type
                ),
            }
            operations.append(operation)
            write_json(out / "requests.json", {"operations": operations})
            step_before = current_snapshot
            await active_client.call_tool(
                "update_hierarchy_xml", {"xml": operation["forward_xml"]}
            )
            current_snapshot = await capture_snapshot(active_client, notebook_id)
            write_json(out / f"forward-{index}.json", current_snapshot)

            try:
                current_target_id, checks = _validate_reparented_snapshot(
                    step_before,
                    current_snapshot,
                    target_id=operation["target_id"],
                    destination_parent_id=operation["destination_parent_id"],
                    resource_type=resource_type,
                )
                operation["current_target_id"] = current_target_id
                if current_target_id not in operation["id_history"]:
                    operation["id_history"].append(current_target_id)
                operation["target_id_changed"] = current_target_id != operation["target_id"]
                operation["forward_id_map"] = {
                    operation["target_id"]: current_target_id
                }
                current_target = find_snapshot_item(current_snapshot, current_target_id)
                if current_target is None:
                    raise InvariantFailure(
                        f"{case} target disappeared before reverse request construction."
                    )
                operation["restore_xml"] = build_reparent_xml(
                    notebook,
                    source,
                    current_target,
                    resource_type,
                )
                write_json(out / "requests.json", {"operations": operations})
                verified[case] = checks
            except InvariantFailure as exc:
                if resource_type == "page":
                    try:
                        operation["current_target_id"] = _locate_reparented_page_id(
                            step_before,
                            current_snapshot,
                            target_id=operation["target_id"],
                            destination_parent_id=operation["destination_parent_id"],
                        )
                    except InvariantFailure:
                        raise exc
                else:
                    observed = find_snapshot_item(
                        current_snapshot, operation["target_id"]
                    )
                    if snapshot_ids(before) != snapshot_ids(current_snapshot) or observed is None:
                        raise
                try:
                    await restore_operations(
                        active_client, current_snapshot, operations
                    )
                except Exception as restore_exc:
                    if isinstance(restore_exc, RestoreFailure):
                        raise
                    raise RestoreFailure(
                        f"Reparent validation failed and restoration also failed: {restore_exc}"
                    ) from restore_exc
                raise exc

        after = current_snapshot
        write_json(out / "after.json", after)

        if getattr(args, "keep_worksite", False):
            worksite = {
                "status": "preserved_after_reparent",
                "target_ids": [operation["current_target_id"] for operation in operations],
                "operations": [
                    {
                        key: value
                        for key, value in operation.items()
                        if key not in {"forward_xml", "restore_xml"}
                    }
                    for operation in operations
                ],
                "verified": True,
                "manual_cleanup_required": True,
                "cleanup": [
                    f"Reparent {resource_type} {operation['current_target_id']} back to parent "
                    f"{operation['source_parent_id']} after inspection."
                    for operation in reversed(operations)
                ],
            }
            write_json(out / "worksite.json", worksite)
            result = {
                "scenario": scenario_name,
                "status": "passed",
                "operations": worksite["operations"],
                "verified": verified,
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
                "warning": "This probes one installed OneNote/Office combination only.",
            }
            write_json(out / "result.json", result)
            return result

        try:
            await restore_operations(active_client, after, operations)
        except Exception as exc:
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Reparent probe completed but restoration failed: {exc}") from exc
        result = {
            "scenario": scenario_name,
            "status": "passed",
            "operations": [
                {
                    key: value
                    for key, value in operation.items()
                    if key not in {"forward_xml", "restore_xml"}
                }
                for operation in operations
            ],
            "verified": verified,
            "restored": True,
            "worksite_preserved": False,
            "warning": "This probes one installed OneNote/Office combination only.",
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["build_reparent_xml", "execute_reparent_probe"]
