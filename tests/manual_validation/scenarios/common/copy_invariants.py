"""Shared Copy subtree and fixture invariant checks."""

from __future__ import annotations

from typing import Any

from ...runtime import InvariantFailure
from ...test_utils import display_name
from .config import AUTOMATED_COPY_CAPABILITIES


PROTECTED_PAGE_FIELDS = (
    "resource_type",
    "name",
    "title",
    "parent_id",
    "section_id",
    "parent_page_id",
    "page_level",
    "order",
)


def assert_pages_unchanged(
    before: dict[str, Any],
    after: dict[str, Any],
    page_ids: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Require exact topology, stable content, and object identity for named Pages."""

    before_by_id = {str(item.get("id")): item for item in before.get("items", [])}
    after_by_id = {str(item.get("id")): item for item in after.get("items", [])}
    before_hashes = before.get("page_hashes")
    after_hashes = after.get("page_hashes")
    before_objects = before.get("page_objects")
    after_objects = after.get("page_objects")
    if not all(
        isinstance(value, dict)
        for value in (before_hashes, after_hashes, before_objects, after_objects)
    ):
        raise InvariantFailure("Protected Page evidence is incomplete.")
    for page_id in sorted({str(value) for value in page_ids}):
        original = before_by_id.get(page_id)
        current = after_by_id.get(page_id)
        if original is None or current is None:
            raise InvariantFailure(f"Protected Page '{page_id}' is missing.")
        if original.get("resource_type") != "page" or current.get("resource_type") != "page":
            raise InvariantFailure(f"Protected object '{page_id}' is not a Page.")
        if any(
            original.get(field) != current.get(field)
            for field in PROTECTED_PAGE_FIELDS
        ):
            raise InvariantFailure(f"Protected Page '{page_id}' changed topology.")
        if page_id not in before_hashes or page_id not in after_hashes:
            raise InvariantFailure(f"Protected Page '{page_id}' is missing content hash evidence.")
        if before_hashes[page_id] != after_hashes[page_id]:
            raise InvariantFailure(f"Protected Page '{page_id}' changed stable content.")
        if page_id not in before_objects or page_id not in after_objects:
            raise InvariantFailure(f"Protected Page '{page_id}' is missing object evidence.")
        if before_objects[page_id] != after_objects[page_id]:
            raise InvariantFailure(f"Protected Page '{page_id}' changed content-object identity.")


def assert_copy_page_restored(
    before: dict[str, Any],
    restored: dict[str, Any],
    protected_page_ids: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Require exact bundle restoration without trusting unrelated Page text hashes."""

    def stable_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            (
                {key: value for key, value in item.items() if key != "modified"}
                for item in snapshot.get("items", [])
            ),
            key=lambda item: str(item.get("id")),
        )

    if before.get("notebook_ids") != restored.get("notebook_ids"):
        raise InvariantFailure("Restored Copy bundle changed a Notebook identity.")
    if stable_items(before) != stable_items(restored):
        raise InvariantFailure("Restored Copy bundle changed object identity or topology.")
    for evidence_key in ("page_objects", "page_capability_projections"):
        original = before.get(evidence_key)
        current = restored.get(evidence_key)
        if not isinstance(original, dict) or not isinstance(current, dict):
            raise InvariantFailure(
                f"Restored Copy bundle is missing {evidence_key} evidence."
            )
        if original != current:
            raise InvariantFailure(
                f"Restored Copy bundle changed {evidence_key} evidence."
            )
    assert_pages_unchanged(before, restored, protected_page_ids)

def expected_copy_source_items(
    snapshot: dict[str, Any],
    source_id: str,
    include_descendants: bool = True,
) -> list[dict[str, Any]]:
    items = snapshot.get("items", [])
    by_id = {item["id"]: item for item in items}
    source = by_id.get(source_id)
    if source is None:
        raise InvariantFailure(f"Copy source '{source_id}' is missing from the before snapshot.")
    if source["resource_type"] == "page":
        if not include_descendants:
            return [source]
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
    include_descendants: bool = True,
) -> None:
    id_map = copied.get("copy_report", {}).get("id_map")
    if not isinstance(id_map, dict) or not id_map:
        raise InvariantFailure("Copy response does not contain a non-empty id_map.")
    source_items = expected_copy_source_items(before, source_id, include_descendants)
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

    if source_items[0]["resource_type"] == "page" and not include_descendants:
        excluded = expected_copy_source_items(before, source_id, True)[1:]
        stable_fields = (
            "resource_type",
            "title",
            "section_id",
            "parent_page_id",
            "page_level",
            "order",
        )
        for original in excluded:
            current = after_by_id.get(original["id"])
            if current is None or any(
                current.get(field) != original.get(field) for field in stable_fields
            ):
                raise InvariantFailure(
                    "Root-only Page Copy changed or removed an excluded source descendant."
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
    *,
    include_automated_defaults: bool = True,
) -> None:
    capabilities = set(planned.get("content_capabilities", []))
    required = set(required_capabilities or set())
    if include_automated_defaults:
        required |= AUTOMATED_COPY_CAPABILITIES
    missing = sorted(required - capabilities)
    if missing:
        raise InvariantFailure(
            "Copy source is missing required fixture capabilities "
            f"{missing}; run the explicit create scenario again before mutation."
        )
