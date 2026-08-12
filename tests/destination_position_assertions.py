"""Independent expected-value builder for destination-position contracts.

This test helper intentionally does not import the production position projector.
"""

from __future__ import annotations

from typing import Any, Iterable


def expected_destination_position(
    items: Iterable[dict[str, Any]],
    target_id: str,
) -> dict[str, Any]:
    snapshot = list(items)
    matches = [item for item in snapshot if str(item.get("id", "")) == target_id]
    if len(matches) != 1:
        raise AssertionError("Expected snapshot must contain one exact destination target.")
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
            raise AssertionError("Expected Page target must be a destination root Page.")
        siblings = sorted(
            (
                item
                for item in snapshot
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
        parents = [item for item in snapshot if item.get("id") == parent_id]
        if len(parents) != 1 or parents[0].get("resource_type") not in {
            "notebook",
            "section_group",
        }:
            raise AssertionError("Expected container parent is missing or invalid.")
        siblings = [
            item
            for item in snapshot
            if item.get("resource_type") == resource_type
            and item.get("parent_id") == parent_id
        ]
        parent_type = str(parents[0]["resource_type"])
        sibling_scope = "same_type_direct_children"
        sequence_source = "hierarchy_child_order"
    else:
        raise AssertionError(f"Unsupported expected resource type: {resource_type}")
    sibling_ids = [str(item.get("id", "")) for item in siblings]
    if sibling_ids.count(target_id) != 1:
        raise AssertionError("Expected target is absent or duplicated in its sibling scope.")
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


def assert_destination_position_contract(
    response: dict[str, Any],
    items: Iterable[dict[str, Any]],
    target_id: str,
) -> dict[str, Any]:
    expected = expected_destination_position(items, target_id)
    assert response.get("destination_position") == expected
    assert set(response["destination_position"]) == set(expected)
    return expected


def assert_destination_position_mismatch_detected(
    response: dict[str, Any],
    items: Iterable[dict[str, Any]],
    target_id: str,
) -> None:
    """Prove every public field and any unexpected extra field are significant."""

    expected = expected_destination_position(items, target_id)
    mutations: list[dict[str, Any]] = []
    for field in expected:
        missing = dict(expected)
        missing.pop(field)
        mutations.append(missing)
        wrong = dict(expected)
        wrong[field] = "__wrong__"
        mutations.append(wrong)
    mutations.append({**expected, "unexpected": True})
    for actual in mutations:
        try:
            assert_destination_position_contract(
                {**response, "destination_position": actual},
                items,
                target_id,
            )
        except AssertionError:
            continue
        raise AssertionError(f"Malformed destination position was accepted: {actual}")


__all__ = [
    "assert_destination_position_contract",
    "assert_destination_position_mismatch_detected",
    "expected_destination_position",
]
