"""Independent manual-validation projection for mutation destination positions."""

from __future__ import annotations

from typing import Any

from ...runtime import InvariantFailure


def expected_destination_position(
    snapshot: dict[str, Any],
    target_id: str,
) -> dict[str, Any]:
    """Compute expected response evidence without importing production services."""

    items = list(snapshot.get("items", []))
    matches = [item for item in items if str(item.get("id", "")) == target_id]
    if len(matches) != 1:
        raise InvariantFailure("Destination target is missing or duplicated in after evidence.")
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
        if (
            not parent_id
            or int(target.get("page_level", 0)) != 1
            or target.get("parent_page_id") not in {None, ""}
        ):
            raise InvariantFailure("Destination Page target is not a root Page.")
        siblings = sorted(
            (
                item
                for item in items
                if item.get("resource_type") == "page"
                and item.get("section_id") == parent_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        parent_type = "section"
        sibling_scope = "section_page_sequence"
        sequence_source = "page_order"
    elif resource_type in {"section", "section_group"}:
        parent_id = target.get("parent_id")
        parents = [item for item in items if item.get("id") == parent_id]
        if len(parents) != 1 or parents[0].get("resource_type") not in {
            "notebook",
            "section_group",
        }:
            raise InvariantFailure("Destination container parent is missing or invalid.")
        parent_type = str(parents[0]["resource_type"])
        siblings = [
            item
            for item in items
            if item.get("resource_type") == resource_type
            and item.get("parent_id") == parent_id
        ]
        sibling_scope = "same_type_direct_children"
        sequence_source = "hierarchy_child_order"
    else:
        raise InvariantFailure("Destination target has an unsupported resource type.")
    sibling_ids = [str(item.get("id", "")) for item in siblings]
    if sibling_ids.count(target_id) != 1:
        raise InvariantFailure("Destination target is absent from its expected sibling scope.")
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


def assert_destination_position(
    response: dict[str, Any],
    snapshot: dict[str, Any],
    target_id: str,
) -> dict[str, Any]:
    expected = expected_destination_position(snapshot, target_id)
    if response.get("destination_position") != expected:
        raise InvariantFailure(
            "Mutation response destination_position differs from independent after evidence."
        )
    return expected


__all__ = ["assert_destination_position", "expected_destination_position"]
