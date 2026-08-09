"""Shared Copy subtree and fixture invariant checks."""

from __future__ import annotations

from typing import Any

from ...runtime import InvariantFailure
from ...test_utils import display_name
from .config import AUTOMATED_COPY_CAPABILITIES

def expected_copy_source_items(
    snapshot: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    items = snapshot.get("items", [])
    by_id = {item["id"]: item for item in items}
    source = by_id.get(source_id)
    if source is None:
        raise InvariantFailure(f"Copy source '{source_id}' is missing from the before snapshot.")
    if source["resource_type"] == "page":
        pages = sorted(
            (
                item
                for item in items
                if item.get("resource_type") == "page"
                and item.get("section_id") == source.get("section_id")
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        start = next(index for index, item in enumerate(pages) if item["id"] == source_id)
        root_level = int(source.get("page_level", 1))
        selected = [source]
        for item in pages[start + 1 :]:
            if int(item.get("page_level", 1)) <= root_level:
                break
            selected.append(item)
        return selected

    def descendant(item: dict[str, Any]) -> bool:
        parent_id = item.get("parent_id")
        while parent_id:
            if parent_id == source_id:
                return True
            parent = by_id.get(parent_id)
            if parent is None:
                return False
            parent_id = parent.get("parent_id")
        return False

    return [item for item in items if item["id"] == source_id or descendant(item)]


def assert_copy_mapping(
    before: dict[str, Any],
    after: dict[str, Any],
    source_id: str,
    destination_parent_id: str | None,
    destination_name: str,
    copied: dict[str, Any],
) -> None:
    id_map = copied.get("copy_report", {}).get("id_map")
    if not isinstance(id_map, dict) or not id_map:
        raise InvariantFailure("Copy response does not contain a non-empty id_map.")
    source_items = expected_copy_source_items(before, source_id)
    source_by_id = {item["id"]: item for item in source_items}
    if set(id_map) != set(source_by_id):
        raise InvariantFailure("Copy id_map source IDs do not exactly match the planned source subtree.")
    target_ids = list(id_map.values())
    if len(set(target_ids)) != len(target_ids) or set(target_ids) & set(id_map):
        raise InvariantFailure("Copy id_map target IDs are not unique and disjoint from source IDs.")
    after_by_id = {item["id"]: item for item in after.get("items", [])}
    missing = sorted(set(target_ids) - set(after_by_id))
    if missing:
        raise InvariantFailure(f"Copy targets are missing from the after snapshot: {missing}")
    before_ids = {item["id"] for item in before.get("items", [])}
    unexpected_new_ids = (set(after_by_id) - before_ids) - set(target_ids)
    if unexpected_new_ids:
        raise InvariantFailure(
            f"Copy created active objects outside id_map: {sorted(unexpected_new_ids)}"
        )

    source_root = source_by_id[source_id]
    target_root = after_by_id[id_map[source_id]]
    if display_name(target_root) != destination_name:
        raise InvariantFailure("Copy target root name differs from the planned destination name.")
    source_root_level = int(source_root.get("page_level", 1))
    for old_id, new_id in id_map.items():
        source = source_by_id[old_id]
        target = after_by_id[new_id]
        if target.get("resource_type") != source.get("resource_type"):
            raise InvariantFailure("Copy id_map changed a resource type.")
        if old_id != source_id and display_name(target) != display_name(source):
            raise InvariantFailure("Copy changed a non-root resource name.")
        kind = source["resource_type"]
        if kind in {"section", "section_group"}:
            expected_parent = (
                destination_parent_id
                if old_id == source_id
                else id_map.get(source.get("parent_id"))
            )
            if target.get("parent_id") != expected_parent:
                raise InvariantFailure("Copy container parent mapping differs from id_map topology.")
        elif kind == "page":
            expected_section = (
                destination_parent_id
                if source_root["resource_type"] == "page"
                else id_map.get(source.get("section_id"))
            )
            if target.get("section_id") != expected_section:
                raise InvariantFailure("Copied Page is in the wrong target Section.")
            expected_parent_page = id_map.get(source.get("parent_page_id"))
            if target.get("parent_page_id") != expected_parent_page:
                raise InvariantFailure("Copied Page parent relation differs from the source subtree.")
            expected_level = int(source.get("page_level", 1))
            if source_root["resource_type"] == "page":
                expected_level = expected_level - source_root_level + 1
            if int(target.get("page_level", 1)) != expected_level:
                raise InvariantFailure("Copied Page relative page_level differs from the source subtree.")

    source_pages_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in source_items:
        if item["resource_type"] == "page":
            source_pages_by_section.setdefault(str(item.get("section_id")), []).append(item)
    for pages in source_pages_by_section.values():
        expected_ids = [
            id_map[item["id"]]
            for item in sorted(pages, key=lambda item: int(item.get("order", 0)))
        ]
        target_section = after_by_id[expected_ids[0]].get("section_id")
        actual_ids = [
            item["id"]
            for item in sorted(
                (
                    after_by_id[target_id]
                    for target_id in expected_ids
                    if after_by_id[target_id].get("section_id") == target_section
                ),
                key=lambda item: int(item.get("order", 0)),
            )
        ]
        if actual_ids != expected_ids:
            raise InvariantFailure("Copied Page relative order differs from the source subtree.")


def assert_copy_fixture_capabilities(
    planned: dict[str, Any],
    required_capabilities: set[str] | None = None,
) -> None:
    capabilities = set(planned.get("content_capabilities", []))
    required = AUTOMATED_COPY_CAPABILITIES | (required_capabilities or set())
    missing = sorted(required - capabilities)
    if missing:
        raise InvariantFailure(
            "Copy source is missing required fixture capabilities "
            f"{missing}; run the explicit create scenario again before mutation."
        )
