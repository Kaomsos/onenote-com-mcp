"""Pure snapshot and hierarchy invariant utility tests."""

from __future__ import annotations

from tests.manual_validation import test_utils
from tests.manual_validation.test_utils import (
    assert_valid_page_tree,
    comparable_snapshot,
    is_descendant_of,
)


def test_snapshot_comparison_ignores_capture_time_and_item_order() -> None:
    first = {
        "captured_at": "before",
        "notebook_id": "n",
        "items": [{"id": "b"}, {"id": "a"}],
        "page_hashes": {"p": "hash"},
        "page_objects": {"p": []},
    }
    second = {**first, "captured_at": "after", "items": list(reversed(first["items"]))}
    assert comparable_snapshot(first) == comparable_snapshot(second)


def test_page_tree_and_delete_sandbox_ancestry_checks() -> None:
    snapshot = {
        "items": [
            {"id": "sandbox", "resource_type": "section_group", "parent_id": "notebook"},
            {"id": "section", "resource_type": "section", "parent_id": "sandbox"},
            {
                "id": "parent",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "section",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
            {
                "id": "child",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "parent",
                "parent_page_id": "parent",
                "page_level": 2,
                "order": 1,
            },
        ]
    }
    assert_valid_page_tree(snapshot, "section")
    assert test_utils.page_topology(snapshot, "section") == [
        ("parent", "section", 0, 1, None),
        ("child", "section", 1, 2, "parent"),
    ]
    assert is_descendant_of(snapshot, "section", "sandbox") is True
    assert is_descendant_of(snapshot, "section", "unrelated") is False
