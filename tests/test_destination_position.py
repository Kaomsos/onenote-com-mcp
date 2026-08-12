"""Independent contracts for mutation destination-position projection."""

from __future__ import annotations

import pytest

from local_onenote_mcp.services.position import destination_position
from tests.destination_position_assertions import (
    assert_destination_position_mismatch_detected,
)


def _snapshot() -> list[dict]:
    return [
        {"resource_type": "notebook", "id": "nb", "parent_id": None},
        {"resource_type": "section_group", "id": "group-b", "parent_id": "nb"},
        {"resource_type": "section", "id": "section-a", "parent_id": "nb"},
        {"resource_type": "section_group", "id": "group-a", "parent_id": "nb"},
        {"resource_type": "section", "id": "section-b", "parent_id": "nb"},
        {
            "resource_type": "page",
            "id": "page-root",
            "section_id": "section-a",
            "order": 0,
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "resource_type": "page",
            "id": "page-child",
            "section_id": "section-a",
            "order": 1,
            "page_level": 2,
            "parent_page_id": "page-root",
        },
        {
            "resource_type": "page",
            "id": "page-target",
            "section_id": "section-a",
            "order": 2,
            "page_level": 1,
            "parent_page_id": None,
        },
    ]


def test_page_position_uses_complete_flat_section_sequence() -> None:
    assert destination_position(_snapshot(), "page-target") == {
        "status": "observed",
        "resource_type": "page",
        "parent_id": "section-a",
        "parent_type": "section",
        "sibling_scope": "section_page_sequence",
        "index": 2,
        "sibling_count": 3,
        "sequence_source": "page_order",
    }


def test_container_position_filters_same_type_direct_children() -> None:
    assert destination_position(_snapshot(), "section-b") == {
        "status": "observed",
        "resource_type": "section",
        "parent_id": "nb",
        "parent_type": "notebook",
        "sibling_scope": "same_type_direct_children",
        "index": 1,
        "sibling_count": 2,
        "sequence_source": "hierarchy_child_order",
    }
    assert destination_position(_snapshot(), "group-a")["index"] == 1


@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_container_position_supports_nested_parent_and_readback_order(
    resource_type: str,
) -> None:
    items = _snapshot()
    items.extend(
        [
            {
                "resource_type": resource_type,
                "id": "nested-z",
                "name": "03-Z",
                "parent_id": "group-a",
            },
            {
                "resource_type": resource_type,
                "id": "nested-target",
                "name": "02-Target",
                "parent_id": "group-a",
            },
            {
                "resource_type": resource_type,
                "id": "nested-a",
                "name": "01-A",
                "parent_id": "group-a",
            },
        ]
    )

    expected = {
        "status": "observed",
        "resource_type": resource_type,
        "parent_id": "group-a",
        "parent_type": "section_group",
        "sibling_scope": "same_type_direct_children",
        "index": 1,
        "sibling_count": 3,
        "sequence_source": "hierarchy_child_order",
    }
    ordered = [
        item
        for item in items
        if item.get("id") not in {"nested-z", "nested-target", "nested-a"}
    ] + [
        next(item for item in items if item.get("id") == object_id)
        for object_id in ("nested-a", "nested-target", "nested-z")
    ]
    assert destination_position(ordered, "nested-target") == expected


def test_notebook_position_is_not_applicable() -> None:
    assert destination_position(_snapshot(), "nb") == {
        "status": "not_applicable",
        "resource_type": "notebook",
        "reason": "notebook_has_no_hierarchy_parent",
    }


@pytest.mark.parametrize("target_id", ["missing", "duplicate"])
def test_position_rejects_missing_or_duplicate_target(target_id: str) -> None:
    items = _snapshot()
    if target_id == "duplicate":
        items.append(dict(items[-1]))
        target_id = "page-target"
    with pytest.raises(RuntimeError, match="exactly one fresh target"):
        destination_position(items, target_id)


def test_page_position_rejects_non_root_target() -> None:
    with pytest.raises(RuntimeError, match="not a root Page"):
        destination_position(_snapshot(), "page-child")


def test_independent_contract_rejects_every_missing_wrong_or_extra_field() -> None:
    response = {"destination_position": destination_position(_snapshot(), "page-target")}

    assert_destination_position_mismatch_detected(
        response,
        _snapshot(),
        "page-target",
    )
