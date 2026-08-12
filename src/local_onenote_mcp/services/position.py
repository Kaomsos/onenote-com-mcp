"""Read-only projection of a mutation target's observed destination position."""

from __future__ import annotations

from typing import Any, Iterable


def destination_position(
    items: Iterable[dict[str, Any]],
    target_id: str,
) -> dict[str, Any]:
    """Project one fresh target root from a complete typed hierarchy snapshot.

    Page positions use the complete flat Page sequence of the destination Section.
    Container positions use same-type direct children of the destination parent.
    Notebook Copy has no hierarchy parent and therefore has no sibling position.
    """

    snapshot = list(items)
    matches = [item for item in snapshot if str(item.get("id", "")) == target_id]
    if len(matches) != 1:
        raise RuntimeError(
            "Destination position requires exactly one fresh target in the final hierarchy snapshot."
        )
    target = matches[0]
    resource_type = str(target.get("resource_type", ""))
    if resource_type == "notebook":
        return {
            "status": "not_applicable",
            "resource_type": "notebook",
            "reason": "notebook_has_no_hierarchy_parent",
        }

    if resource_type == "page":
        parent_id = target.get("section_id")
        parent_type = "section"
        if not parent_id:
            raise RuntimeError("Destination Page read-back is missing its Section ID.")
        if (
            int(target.get("page_level", 0)) != 1
            or target.get("parent_page_id") not in {None, ""}
        ):
            raise RuntimeError("Destination Page target is not a root Page.")
        siblings = sorted(
            (
                item
                for item in snapshot
                if item.get("resource_type") == "page"
                and item.get("section_id") == parent_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        sibling_scope = "section_page_sequence"
        sequence_source = "page_order"
    elif resource_type in {"section", "section_group"}:
        parent_id = target.get("parent_id")
        if not parent_id:
            raise RuntimeError("Destination container read-back is missing its parent ID.")
        parents = [item for item in snapshot if item.get("id") == parent_id]
        if len(parents) != 1 or parents[0].get("resource_type") not in {
            "notebook",
            "section_group",
        }:
            raise RuntimeError("Destination container parent is missing or has an invalid type.")
        parent_type = str(parents[0]["resource_type"])
        siblings = [
            item
            for item in snapshot
            if item.get("resource_type") == resource_type
            and item.get("parent_id") == parent_id
        ]
        sibling_scope = "same_type_direct_children"
        sequence_source = "hierarchy_child_order"
    else:
        raise RuntimeError(f"Unsupported destination position resource type '{resource_type}'.")

    sibling_ids = [str(item.get("id", "")) for item in siblings]
    if sibling_ids.count(target_id) != 1:
        raise RuntimeError("Fresh destination target is missing or duplicated in its sibling sequence.")
    return {
        "status": "observed",
        "resource_type": resource_type,
        "parent_id": str(parent_id),
        "parent_type": parent_type,
        "sibling_scope": sibling_scope,
        "index": sibling_ids.index(target_id),
        "sibling_count": len(sibling_ids),
        "sequence_source": sequence_source,
    }


def unavailable_destination_position(
    resource_type: str,
    reason: str,
) -> dict[str, str]:
    """Return the stable partial-outcome shape when no trustworthy index exists."""

    return {
        "status": "unavailable",
        "resource_type": resource_type,
        "reason": reason,
    }
