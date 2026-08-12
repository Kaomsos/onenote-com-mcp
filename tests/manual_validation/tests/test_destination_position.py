"""Pure contracts for the manual validator's independent position projector."""

from __future__ import annotations

import pytest

from tests.manual_validation.runtime import InvariantFailure
from tests.manual_validation.scenarios.common.destination_position import (
    assert_destination_position,
    expected_destination_position,
)


def _snapshot() -> dict:
    return {
        "items": [
            {"resource_type": "notebook", "id": "nb", "parent_id": None},
            {"resource_type": "section", "id": "section", "parent_id": "nb"},
            {"resource_type": "section", "id": "section-target", "parent_id": "nb"},
            {"resource_type": "page", "id": "anchor", "section_id": "section", "page_level": 1, "parent_page_id": None, "order": 0},
            {"resource_type": "page", "id": "target", "section_id": "section", "page_level": 1, "parent_page_id": None, "order": 1},
        ]
    }


def test_independent_projector_describes_root_page_without_level() -> None:
    expected = expected_destination_position(_snapshot(), "target")

    assert expected["index"] == 1
    assert expected["sibling_count"] == 2
    assert "page_level" not in expected
    assert "level" not in expected


def test_independent_projector_covers_container_and_notebook_shapes() -> None:
    snapshot = _snapshot()

    assert expected_destination_position(snapshot, "section-target")["index"] == 1
    assert expected_destination_position(snapshot, "nb") == {
        "status": "not_applicable",
        "resource_type": "notebook",
        "reason": "notebook_has_no_hierarchy_parent",
    }


def test_response_mismatch_fails_manual_evidence_contract() -> None:
    with pytest.raises(InvariantFailure, match="differs"):
        assert_destination_position(
            {"destination_position": {"status": "observed", "index": 0}},
            _snapshot(),
            "target",
        )
