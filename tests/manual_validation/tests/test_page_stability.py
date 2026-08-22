from __future__ import annotations

import asyncio

import pytest

from tests.manual_validation.page_stability import (
    PageStabilityError,
    observe_forward_rename_durability,
    page_identity_from_expand,
    wait_for_stable_page_baseline,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _tree(title: str, *, modified: str = "2026-08-22T00:00:00Z") -> dict:
    return {
        "tree": {
            "item": {
                "id": "page-id",
                "resource_type": "page",
                "title": title,
                "section_id": "section-id",
                "parent_id": "section-id",
                "modified": modified,
            },
            "children": [],
        }
    }


def test_page_identity_from_expand_requires_exact_page_tree() -> None:
    identity = page_identity_from_expand(_tree("00-Owned-Page"), page_id="page-id")
    assert identity.signature() == (
        "page-id",
        "00-Owned-Page",
        "section-id",
        "section-id",
        "2026-08-22T00:00:00Z",
    )
    with pytest.raises(ValueError, match="different Page ID"):
        page_identity_from_expand(_tree("00-Owned-Page"), page_id="other-id")


def test_baseline_requires_consecutive_title_id_parent_modified() -> None:
    clock = _Clock()
    titles = ["00-Owned-Page", "00-Owned-Page", "00-Owned-Page"]

    async def observe():
        return _tree(titles.pop(0))

    result = asyncio.run(
        wait_for_stable_page_baseline(
            observe,
            page_id="page-id",
            expected_title="00-Owned-Page",
            expected_parent_id="section-id",
            expected_section_id="section-id",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    )
    assert result["status"] == "stable"
    assert result["attempts"] == 3
    assert result["xml_recorded"] is False
    assert clock.sleeps == [1.0, 1.0]


def test_forward_original_marker_marker_original_is_not_durable() -> None:
    clock = _Clock()
    titles = ["00-Owned-Page", "COM-REFRESH-MARK", "COM-REFRESH-MARK", "00-Owned-Page"]
    observe_calls = 0

    async def observe():
        nonlocal observe_calls
        observe_calls += 1
        return _tree(titles.pop(0))

    with pytest.raises(PageStabilityError, match="reverted to the original title") as caught:
        asyncio.run(
            observe_forward_rename_durability(
                observe,
                page_id="page-id",
                marker_title="COM-REFRESH-MARK",
                original_title="00-Owned-Page",
                expected_parent_id="section-id",
                expected_section_id="section-id",
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        )
    evidence = caught.value.evidence
    assert evidence["status"] == "forward_not_durable"
    assert evidence["seen_marker"] is True
    assert evidence["reverted_to_original"] is True
    assert evidence["xml_recorded"] is False
    assert observe_calls == 4
    assert [item["title_matches_original"] for item in evidence["observations"]] == [
        True,
        False,
        False,
        True,
    ]
    assert [item["title_matches_marker"] for item in evidence["observations"]] == [
        False,
        True,
        True,
        False,
    ]


def test_forward_marker_stables_plus_linger_are_durable() -> None:
    clock = _Clock()
    titles = ["COM-REFRESH-MARK"] * 4

    async def observe():
        return _tree(titles.pop(0))

    result = asyncio.run(
        observe_forward_rename_durability(
            observe,
            page_id="page-id",
            marker_title="COM-REFRESH-MARK",
            original_title="00-Owned-Page",
            expected_parent_id="section-id",
            expected_section_id="section-id",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    )
    assert result["status"] == "durable"
    assert result["attempts"] == 4
    assert result["reverted_to_original"] is False
    assert result["xml_recorded"] is False
    assert clock.sleeps == [1.0, 1.0, 1.0]
