"""Contracts for typed same-Notebook Reparent tools."""

from __future__ import annotations

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.services.errors import PartialFailure
from tests.destination_position_assertions import assert_destination_position_contract


def _page_xml(page_id: str, title: str) -> str:
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        f'ID="{page_id}" name="{title}"><one:Outline objectID="outline-{page_id}">'
        f'<one:OEChildren><one:OE><one:T>{title}</one:T></one:OE></one:OEChildren>'
        '</one:Outline></one:Page>'
    )


def _page_scope_before() -> dict:
    items = [
        {"resource_type": "notebook", "id": "notebook-id", "name": "Notebook", "parent_id": None},
        {"resource_type": "section", "id": "source-section", "name": "Source", "parent_id": "notebook-id", "notebook_id": "notebook-id"},
        {"resource_type": "section", "id": "destination-section", "name": "Destination", "parent_id": "notebook-id", "notebook_id": "notebook-id"},
        {"resource_type": "page", "id": "source-parent", "title": "Source Parent", "parent_id": "source-section", "section_id": "source-section", "notebook_id": "notebook-id", "page_level": 1, "parent_page_id": None, "order": 0},
        {"resource_type": "page", "id": "selected", "title": "Selected", "parent_id": "source-section", "section_id": "source-section", "notebook_id": "notebook-id", "page_level": 2, "parent_page_id": "source-parent", "order": 1},
        {"resource_type": "page", "id": "child", "title": "Child", "parent_id": "source-section", "section_id": "source-section", "notebook_id": "notebook-id", "page_level": 3, "parent_page_id": "selected", "order": 2},
        {"resource_type": "page", "id": "grandchild", "title": "Grandchild", "parent_id": "source-section", "section_id": "source-section", "notebook_id": "notebook-id", "page_level": 4, "parent_page_id": "child", "order": 3},
        {"resource_type": "page", "id": "source-after", "title": "Source After", "parent_id": "source-section", "section_id": "source-section", "notebook_id": "notebook-id", "page_level": 2, "parent_page_id": "source-parent", "order": 4},
        {"resource_type": "page", "id": "destination-anchor", "title": "Destination Anchor", "parent_id": "destination-section", "section_id": "destination-section", "notebook_id": "notebook-id", "page_level": 1, "parent_page_id": None, "order": 0},
    ]
    return {
        "items": items,
        "page_xml": {
            item["id"]: _page_xml(item["id"], item["title"])
            for item in items
            if item["resource_type"] == "page"
        },
    }


def _branched_page_scope_before() -> dict:
    snapshot = _page_scope_before()
    branch = {
        "resource_type": "page",
        "id": "branch",
        "title": "Branch",
        "parent_id": "source-section",
        "section_id": "source-section",
        "notebook_id": "notebook-id",
        "page_level": 3,
        "parent_page_id": "selected",
        "order": 4,
    }
    for item in snapshot["items"]:
        if item.get("resource_type") == "page" and int(item.get("order", 0)) >= 4:
            item["order"] = int(item["order"]) + 1
    snapshot["items"].append(branch)
    snapshot["page_xml"]["branch"] = _page_xml("branch", "Branch")
    return snapshot


def _remap_xml(xml: str, old_id: str, new_id: str) -> str:
    return xml.replace(old_id, new_id)


def _container_items() -> list[dict]:
    return [
        {
            "resource_type": "notebook",
            "id": "notebook-id",
            "name": "Notebook",
            "parent_id": None,
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "section_group",
            "id": "source-group-id",
            "name": "Source",
            "parent_id": "notebook-id",
            "notebook_id": "notebook-id",
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "section_group",
            "id": "destination-group-id",
            "name": "Destination",
            "parent_id": "notebook-id",
            "notebook_id": "notebook-id",
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "section",
            "id": "section-id",
            "name": "Section",
            "parent_id": "source-group-id",
            "notebook_id": "notebook-id",
            "modified": "modified",
            "is_in_recycle_bin": False,
        },
    ]


@pytest.mark.write_contract
def test_reparent_section_rejects_cross_notebook_destination_before_com(monkeypatch) -> None:
    items = _container_items()
    items.append(
        {
            "resource_type": "section_group",
            "id": "other-group-id",
            "name": "Other",
            "parent_id": "other-notebook-id",
            "notebook_id": "other-notebook-id",
            "is_in_recycle_bin": False,
        }
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-Notebook rejection must happen before UpdateHierarchy")
        ),
    )

    with pytest.raises(ValueError, match="same notebook"):
        server.services.mutations.reparent_section(
            "section-id", "other-group-id", "Section", "source-group-id", "modified"
        )


@pytest.mark.write_contract
def test_reparent_section_group_rejects_self_or_descendant_before_com(monkeypatch) -> None:
    items = _container_items()
    items.extend(
        [
            {
                "resource_type": "section_group",
                "id": "target-group-id",
                "name": "Target",
                "parent_id": "source-group-id",
                "notebook_id": "notebook-id",
                "is_in_recycle_bin": False,
            },
            {
                "resource_type": "section_group",
                "id": "child-group-id",
                "name": "Child",
                "parent_id": "target-group-id",
                "notebook_id": "notebook-id",
                "is_in_recycle_bin": False,
            },
        ]
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cycle rejection must happen before UpdateHierarchy")
        ),
    )

    with pytest.raises(ValueError, match="itself or its descendant"):
        server.services.mutations.reparent_section_group(
            "target-group-id", "child-group-id", "Target", "source-group-id"
        )


@pytest.mark.write_contract
def test_reparent_page_rejects_wrong_destination_type_before_com(monkeypatch) -> None:
    items = _container_items()
    items.append(
        {
            "resource_type": "page",
            "id": "page-id",
            "title": "Page",
            "parent_id": "section-id",
            "section_id": "section-id",
            "notebook_id": "notebook-id",
            "page_level": 1,
            "parent_page_id": None,
            "is_in_recycle_bin": False,
        }
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("typed destination rejection must happen before UpdateHierarchy")
        ),
    )

    with pytest.raises(ValueError, match="destination_section_id"):
        server.services.mutations.reparent_page(
            "page-id", "destination-group-id", "Page", "section-id"
        )


@pytest.mark.write_contract
def test_reparent_rejects_stale_confirmation_before_com(monkeypatch) -> None:
    items = _container_items()
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale confirmation must fail before UpdateHierarchy")
        ),
    )

    with pytest.raises(ValueError, match="expected modified 'stale'"):
        server.services.mutations.reparent_section(
            "section-id", "destination-group-id", "Section", "source-group-id", "stale"
        )


@pytest.mark.write_contract
def test_reparent_page_validator_reports_page_and_content_id_remaps() -> None:
    before_items = [
        {
            "resource_type": "notebook",
            "id": "notebook-id",
            "name": "Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Source",
            "parent_id": "notebook-id",
            "notebook_id": "notebook-id",
        },
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Destination",
            "parent_id": "notebook-id",
            "notebook_id": "notebook-id",
        },
        {
            "resource_type": "page",
            "id": "old-page",
            "title": "Page",
            "parent_id": "source-section",
            "section_id": "source-section",
            "notebook_id": "notebook-id",
            "page_level": 1,
            "parent_page_id": None,
        },
    ]
    after_items = [dict(item) for item in before_items[:-1]] + [
        {
            **before_items[-1],
            "id": "new-page",
            "parent_id": "destination-section",
            "section_id": "destination-section",
        }
    ]
    before_xml = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        'ID="old-page" name="Page"><one:Outline objectID="old-outline">'
        '<one:OEChildren><one:OE objectID="old-oe"><one:T>Text</one:T></one:OE>'
        '</one:OEChildren></one:Outline></one:Page>'
    )
    after_xml = before_xml.replace("old-page", "new-page").replace(
        "old-outline", "new-outline"
    ).replace("old-oe", "new-oe")

    item, id_map, verified = server.services.mutations._validate_reparent_snapshots(
        {"items": before_items, "page_xml": {"old-page": before_xml}},
        {"items": after_items, "page_xml": {"new-page": after_xml}},
        target_id="old-page",
        destination_parent_id="destination-section",
        resource_type="page",
    )

    assert item["id"] == "new-page"
    assert id_map == {
        "old-page": "new-page",
        "old-outline": "new-outline",
        "old-oe": "new-oe",
    }
    assert all(verified.values())


@pytest.mark.write_contract
def test_reparent_page_root_only_promotes_excluded_descendants_and_reports_root_position() -> None:
    before = _page_scope_before()
    after_items = []
    for item in before["items"]:
        if item["id"] == "selected":
            continue
        current = dict(item)
        if item["id"] in {"child", "grandchild"}:
            current["page_level"] -= 1
            current["parent_page_id"] = "source-parent" if item["id"] == "child" else "child"
        after_items.append(current)
    after_items.append(
        {
            **next(item for item in before["items"] if item["id"] == "selected"),
            "id": "selected-new",
            "parent_id": "destination-section",
            "section_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
            "order": 1,
        }
    )
    after = {
        "items": after_items,
        "page_xml": {
            **{
                page_id: xml
                for page_id, xml in before["page_xml"].items()
                if page_id != "selected"
            },
            "selected-new": _remap_xml(
                before["page_xml"]["selected"], "selected", "selected-new"
            ),
        },
    }

    item, id_map, verified = server.services.mutations._validate_reparent_page_scope(
        before,
        after,
        selected=[next(value for value in before["items"] if value["id"] == "selected")],
        destination_section_id="destination-section",
        include_descendants=False,
    )

    assert item["id"] == "selected-new"
    assert id_map["selected"] == "selected-new"
    assert all(verified.values())
    position = assert_destination_position_contract(
        {"destination_position": {
            "status": "observed",
            "resource_type": "page",
            "parent_id": "destination-section",
            "parent_type": "section",
            "sibling_scope": "section_page_sequence",
            "index": 1,
            "sibling_count": 2,
            "sequence_source": "page_order",
        }},
        after["items"],
        item["id"],
    )
    assert position["index"] == 1
    assert position["sibling_count"] == 2
    assert "page_level" not in position


@pytest.mark.write_contract
def test_reparent_page_full_subtree_normalizes_root_and_maps_every_page() -> None:
    before = _page_scope_before()
    selected = server.services.mutations._page_scope(before["items"], "selected")
    selected_ids = {item["id"] for item in selected}
    after_items = [dict(item) for item in before["items"] if item["id"] not in selected_ids]
    after_xml = {page_id: xml for page_id, xml in before["page_xml"].items() if page_id not in selected_ids}
    previous_id = None
    for order, source in enumerate(selected, start=1):
        current_id = f"new-{source['id']}"
        after_items.append(
            {
                **source,
                "id": current_id,
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "page_level": int(source["page_level"]) - 1,
                "parent_page_id": previous_id,
                "order": order,
            }
        )
        after_xml[current_id] = _remap_xml(
            before["page_xml"][source["id"]], source["id"], current_id
        )
        previous_id = current_id
    after = {"items": after_items, "page_xml": after_xml}

    item, id_map, verified = server.services.mutations._validate_reparent_page_scope(
        before,
        after,
        selected=selected,
        destination_section_id="destination-section",
        include_descendants=True,
    )

    assert item["id"] == "new-selected"
    assert [id_map[source["id"]] for source in selected] == [
        "new-selected",
        "new-child",
        "new-grandchild",
    ]
    assert all(verified.values())
    position = assert_destination_position_contract(
        {"destination_position": {
            "status": "observed",
            "resource_type": "page",
            "parent_id": "destination-section",
            "parent_type": "section",
            "sibling_scope": "section_page_sequence",
            "index": 1,
            "sibling_count": 4,
            "sequence_source": "page_order",
        }},
        after["items"],
        item["id"],
    )
    assert position["index"] == 1
    assert position["sibling_count"] == 4
    assert "page_level" not in position


@pytest.mark.write_contract
@pytest.mark.parametrize("include_descendants", [False, True])
def test_reparent_page_preserves_branched_scope_topology(
    include_descendants: bool,
) -> None:
    before = _branched_page_scope_before()
    complete_scope = server.services.mutations._page_scope(
        before["items"], "selected"
    )
    assert [item["id"] for item in complete_scope] == [
        "selected",
        "child",
        "grandchild",
        "branch",
    ]
    selected = complete_scope if include_descendants else complete_scope[:1]
    selected_ids = {str(item["id"]) for item in selected}
    after_items = [
        dict(item) for item in before["items"] if str(item["id"]) not in selected_ids
    ]
    after_xml = {
        page_id: xml
        for page_id, xml in before["page_xml"].items()
        if page_id not in selected_ids
    }
    if not include_descendants:
        for item in after_items:
            if item.get("id") in {"child", "grandchild", "branch"}:
                item["page_level"] = int(item["page_level"]) - 1
        expected_parents = server.services.mutations._page_parent_map(
            sorted(
                (
                    item
                    for item in after_items
                    if item.get("resource_type") == "page"
                    and item.get("section_id") == "source-section"
                ),
                key=lambda item: int(item["order"]),
            )
        )
        for item in after_items:
            if item.get("resource_type") == "page" and item.get("section_id") == "source-section":
                item["parent_page_id"] = expected_parents[str(item["id"])]

    root_level = int(complete_scope[0]["page_level"])
    remapped_scope = []
    for order, source in enumerate(selected, start=2):
        current_id = f"new-{source['id']}"
        remapped_scope.append(
            {
                **source,
                "id": current_id,
                "parent_id": "destination-section",
                "section_id": "destination-section",
                "page_level": int(source["page_level"]) - root_level + 1,
                "order": order,
            }
        )
        after_xml[current_id] = _remap_xml(
            before["page_xml"][str(source["id"])], str(source["id"]), current_id
        )
    remapped_parents = server.services.mutations._page_parent_map(remapped_scope)
    for item in remapped_scope:
        item["parent_page_id"] = remapped_parents[str(item["id"])]
    after_items.extend(remapped_scope)
    after = {"items": after_items, "page_xml": after_xml}

    root, id_map, verified = server.services.mutations._validate_reparent_page_scope(
        before,
        after,
        selected=selected,
        destination_section_id="destination-section",
        include_descendants=include_descendants,
    )

    expected_page_ids = [str(item["id"]) for item in selected]
    assert [id_map[page_id] for page_id in expected_page_ids] == [
        f"new-{page_id}" for page_id in expected_page_ids
    ]
    assert all(verified.values())
    assert root["id"] == "new-selected"
    assert_destination_position_contract(
        {
            "destination_position": {
                "status": "observed",
                "resource_type": "page",
                "parent_id": "destination-section",
                "parent_type": "section",
                "sibling_scope": "section_page_sequence",
                "index": 1,
                "sibling_count": 1 + len(remapped_scope),
                "sequence_source": "page_order",
            }
        },
        after_items,
        "new-selected",
    )


@pytest.mark.write_contract
def test_reparent_page_reports_structured_partial_after_promotion_when_reparent_fails(monkeypatch) -> None:
    items = _page_scope_before()["items"]
    target = next(item for item in items if item["id"] == "selected")
    target["modified"] = "modified"
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    before = _page_scope_before()
    next(item for item in before["items"] if item["id"] == "selected")["modified"] = "modified"
    monkeypatch.setattr(server.services.mutations, "_capture_reparent_snapshot", lambda _id: before)
    monkeypatch.setattr(
        server.services.mutations,
        "_promote_reparent_descendants",
        lambda *_args: (before, {"promoted": True, "preserved_descendant_ids": ["child", "grandchild"]}),
    )
    monkeypatch.setattr(server.services.hierarchy, "reparent_page_scope_xml", lambda *_args, **_kwargs: "<typed />")
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("COM failed")),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.reparent_page(
            "selected",
            "destination-section",
            "Selected",
            "source-section",
            "modified",
        )

    assert caught.value.details["outcome"] == "descendants_promoted_reparent_not_completed"
    assert caught.value.details["destination_position"] == {
        "status": "unavailable",
        "resource_type": "page",
        "reason": "destination_target_not_uniquely_observed",
    }
    assert caught.value.details["active_source_ids"] == ["selected"]
    assert caught.value.details["observed_destination_ids"] == []


@pytest.mark.write_contract
@pytest.mark.parametrize("failure_stage", ["promotion_call", "promotion_readback"])
def test_reparent_page_reports_promotion_failure_before_reparent(
    monkeypatch,
    failure_stage: str,
) -> None:
    before = _page_scope_before()
    next(item for item in before["items"] if item["id"] == "selected")[
        "modified"
    ] = "modified"
    captures = 0
    calls: list[str] = []

    def capture(_notebook_id: str) -> dict:
        nonlocal captures
        captures += 1
        if failure_stage == "promotion_readback" and captures == 2:
            raise RuntimeError("promotion read-back failed")
        return before

    def call(operation: str, **_kwargs) -> dict:
        calls.append(operation)
        if operation == "update_hierarchy" and failure_stage == "promotion_call":
            raise RuntimeError("promotion call failed")
        return {}

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy, "resources", lambda **_kwargs: before["items"]
    )
    monkeypatch.setattr(server.services.mutations, "_capture_reparent_snapshot", capture)
    monkeypatch.setattr(server.services.mutations, "call", call)
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Reparent XML must not be built after promotion failure")
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.reparent_page(
            "selected",
            "destination-section",
            "Selected",
            "source-section",
            "modified",
        )

    assert calls == ["update_hierarchy"]
    assert caught.value.details["outcome"] == "descendant_promotion_unverified"
    assert caught.value.details["reparent_attempted"] is False
    assert caught.value.details["preserved_descendant_ids"] == [
        "child",
        "grandchild",
    ]
    assert caught.value.details["destination_position"] == {
        "status": "unavailable",
        "resource_type": "page",
        "reason": "destination_target_not_created",
    }


@pytest.mark.write_contract
def test_reparent_page_rejects_semantic_snapshot_change_before_first_mutation(monkeypatch) -> None:
    items = _page_scope_before()["items"]
    target = next(item for item in items if item["id"] == "selected")
    target["modified"] = "modified"
    changed = _page_scope_before()
    changed_target = next(
        item for item in changed["items"] if item["id"] == "selected"
    )
    changed_target["title"] = "Changed After Confirmation"
    calls: list[str] = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: changed,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {},
    )

    with pytest.raises(RuntimeError, match="changed after Reparent confirmation"):
        server.services.mutations.reparent_page(
            "selected",
            "destination-section",
            "Selected",
            "source-section",
            "modified",
        )

    assert calls == []


@pytest.mark.write_contract
def test_reparent_page_allows_modified_clock_drift_before_first_mutation(monkeypatch) -> None:
    items = _page_scope_before()["items"]
    target = next(item for item in items if item["id"] == "selected")
    target["modified"] = "modified"
    changed = _page_scope_before()
    changed_target = next(
        item for item in changed["items"] if item["id"] == "selected"
    )
    changed_target["modified"] = "one-note-clock-drift"
    destination = next(
        item for item in changed["items"] if item["id"] == "destination-section"
    )
    destination["modified"] = "destination-clock-drift"
    calls: list[str] = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: changed,
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page-scope />",
    )

    def stop_after_mutation(operation, **_kwargs):
        calls.append(operation)
        raise RuntimeError("stop after first mutation")

    monkeypatch.setattr(server.services.mutations, "call", stop_after_mutation)

    with pytest.raises(RuntimeError, match="stop after first mutation"):
        server.services.mutations.reparent_page(
            "selected",
            "destination-section",
            "Selected",
            "source-section",
            "modified",
            True,
        )

    assert calls == ["update_hierarchy"]


@pytest.mark.write_contract
def test_reparent_page_partial_reports_observed_root_when_subtree_is_incomplete(
    monkeypatch,
) -> None:
    before = _page_scope_before()
    target = next(item for item in before["items"] if item["id"] == "selected")
    target["modified"] = "modified"
    selected_scope = {"selected", "child", "grandchild"}
    candidate_items = [
        dict(item) for item in before["items"] if item["id"] not in selected_scope
    ]
    candidate_items.append(
        {
            **target,
            "id": "selected-new",
            "parent_id": "destination-section",
            "section_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
            "order": 1,
        }
    )
    candidate = {
        "items": candidate_items,
        "page_xml": {
            **{
                page_id: xml
                for page_id, xml in before["page_xml"].items()
                if page_id not in selected_scope
            },
            "selected-new": _remap_xml(
                before["page_xml"]["selected"], "selected", "selected-new"
            ),
        },
    }
    captures = iter([before, candidate])
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda **_kwargs: before["items"],
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: candidate["items"],
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: next(captures),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page-scope />",
    )
    monkeypatch.setattr(server.services.mutations, "call", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.sleep", lambda _seconds: None
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.reparent_page(
            "selected",
            "destination-section",
            "Selected",
            "source-section",
            "modified",
            True,
        )

    assert caught.value.details["outcome"] == "reparent_subtree_incomplete"
    assert caught.value.details["active_source_ids"] == []
    assert caught.value.details["observed_destination_ids"] == ["selected-new"]
    assert_destination_position_contract(
        {"destination_position": caught.value.details["destination_position"]},
        candidate_items,
        "selected-new",
    )


@pytest.mark.write_contract
def test_reparent_page_readback_unavailable_reports_stable_partial_reason(
    monkeypatch,
) -> None:
    before = _page_scope_before()
    target = next(item for item in before["items"] if item["id"] == "source-after")
    target["modified"] = "modified"
    after_items = [
        dict(item) for item in before["items"] if item["id"] != "source-after"
    ]
    after_items.append(
        {
            **target,
            "id": "source-after-new",
            "parent_id": "destination-section",
            "section_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
            "order": 1,
        }
    )
    captures = 0

    def capture(_notebook_id: str) -> dict:
        nonlocal captures
        captures += 1
        if captures == 1:
            return before
        raise RuntimeError("destination snapshot unavailable")

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy, "resources", lambda **_kwargs: before["items"]
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: after_items,
    )
    monkeypatch.setattr(server.services.mutations, "_capture_reparent_snapshot", capture)
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page-scope />",
    )
    monkeypatch.setattr(server.services.mutations, "call", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.sleep", lambda _seconds: None
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.reparent_page(
            "source-after",
            "destination-section",
            "Source After",
            "source-section",
            "modified",
        )

    assert captures == 3
    assert caught.value.details["outcome"] == "reparent_subtree_incomplete"
    assert caught.value.details["readback_phase"] == "full_evidence_capture"
    assert caught.value.details["capture_attempts"] == 2
    assert caught.value.details["mutation_replayed"] is False
    assert caught.value.details["active_source_ids"] == []
    assert caught.value.details["observed_destination_ids"] == ["source-after-new"]
    assert_destination_position_contract(
        {"destination_position": caught.value.details["destination_position"]},
        after_items,
        "source-after-new",
    )


@pytest.mark.write_contract
def test_reparent_fails_closed_when_com_succeeds_without_state_change(monkeypatch) -> None:
    items = _container_items()
    snapshot = {"items": items, "page_xml": {}}
    calls: list[str] = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: snapshot,
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_xml",
        lambda *_args, **_kwargs: "<typed-service-generated />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {"updated": True},
    )
    monkeypatch.setattr("local_onenote_mcp.services.mutations.time.sleep", lambda _seconds: None)

    with pytest.raises(PartialFailure, match="hierarchy convergence") as caught:
        server.services.mutations.reparent_section(
            "section-id", "destination-group-id", "Section", "source-group-id", "modified"
        )
    assert calls == ["update_hierarchy"]
    assert caught.value.details["readback_phase"] == "hierarchy_convergence"
    assert caught.value.details["readback_error"]
    assert caught.value.details["mutation_replayed"] is False


@pytest.mark.write_contract
@pytest.mark.parametrize(
    "resource_type,destination_parent_id",
    [
        ("section", "notebook-id"),
        ("section", "destination-group-id"),
        ("section_group", "notebook-id"),
        ("section_group", "destination-group-id"),
    ],
)
def test_reparent_container_service_returns_observed_destination_position(
    monkeypatch,
    resource_type: str,
    destination_parent_id: str,
) -> None:
    before_items = _container_items()
    if resource_type == "section":
        target_id = "section-id"
        expected_name = "Section"
        source_parent_id = "source-group-id"
        invoke = server.services.mutations.reparent_section
    else:
        target_id = "target-group-id"
        expected_name = "Target"
        source_parent_id = "source-group-id"
        before_items.append(
            {
                "resource_type": "section_group",
                "id": target_id,
                "name": expected_name,
                "parent_id": source_parent_id,
                "notebook_id": "notebook-id",
                "modified": "modified",
                "is_in_recycle_bin": False,
            }
        )
        invoke = server.services.mutations.reparent_section_group
    before_items.extend(
        [
            {
                "resource_type": resource_type,
                "id": f"{resource_type}-anchor-alpha",
                "name": "Alpha",
                "parent_id": destination_parent_id,
                "notebook_id": "notebook-id",
                "is_in_recycle_bin": False,
            },
            {
                "resource_type": resource_type,
                "id": f"{resource_type}-anchor-zulu",
                "name": "Zulu",
                "parent_id": destination_parent_id,
                "notebook_id": "notebook-id",
                "is_in_recycle_bin": False,
            },
        ]
    )
    moved = {
        **next(item for item in before_items if item["id"] == target_id),
        "parent_id": destination_parent_id,
    }
    after_items = [dict(item) for item in before_items if item["id"] != target_id]
    zulu_index = next(
        index
        for index, item in enumerate(after_items)
        if item["id"] == f"{resource_type}-anchor-zulu"
    )
    after_items.insert(zulu_index, moved)
    snapshots = iter(
        [
            {"items": before_items, "page_xml": {}},
            {"items": after_items, "page_xml": {}},
        ]
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda **_kwargs: before_items,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: after_items,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: next(snapshots),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_xml",
        lambda *_args, **_kwargs: "<typed-service-generated />",
    )
    monkeypatch.setattr(server.services.mutations, "call", lambda *_args, **_kwargs: {})

    result = invoke(
        target_id,
        destination_parent_id,
        expected_name,
        source_parent_id,
        "modified",
    )

    assert result["item"]["id"] == target_id
    position = assert_destination_position_contract(result, after_items, target_id)
    destination_siblings = [
        item
        for item in after_items
        if item.get("resource_type") == resource_type
        and item.get("parent_id") == destination_parent_id
    ]
    destination_ids = [str(item["id"]) for item in destination_siblings]
    assert destination_ids.index(target_id) == destination_ids.index(
        f"{resource_type}-anchor-alpha"
    ) + 1
    assert destination_ids.index(f"{resource_type}-anchor-zulu") == (
        destination_ids.index(target_id) + 1
    )
    assert position["sibling_count"] == len(destination_siblings)


@pytest.mark.write_contract
def test_reparent_page_service_returns_only_normalized_root_position(monkeypatch) -> None:
    before = _page_scope_before()
    selected = next(item for item in before["items"] if item["id"] == "source-after")
    after_items = [dict(item) for item in before["items"] if item["id"] != "source-after"]
    after_items.append(
        {
            **selected,
            "id": "source-after-new",
            "parent_id": "destination-section",
            "section_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
            "order": 1,
        }
    )
    after = {
        "items": after_items,
        "page_xml": {
            **{
                page_id: xml
                for page_id, xml in before["page_xml"].items()
                if page_id != "source-after"
            },
            "source-after-new": _remap_xml(
                before["page_xml"]["source-after"],
                "source-after",
                "source-after-new",
            ),
        },
    }
    snapshots = iter([before, after])
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda **_kwargs: before["items"],
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: after_items,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: next(snapshots),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page-scope />",
    )
    monkeypatch.setattr(server.services.mutations, "call", lambda *_args, **_kwargs: {})

    result = server.services.mutations.reparent_page(
        "source-after",
        "destination-section",
        "Source After",
        "source-section",
    )

    assert result["include_descendants"] is False
    assert_destination_position_contract(result, after_items, "source-after-new")
    assert "level" not in result["destination_position"]
    assert "page_level" not in result["destination_position"]


@pytest.mark.write_contract
@pytest.mark.parametrize("explicit_false", [False, True])
def test_reparent_page_without_descendants_skips_promotion_and_false_is_equivalent(
    monkeypatch,
    explicit_false: bool,
) -> None:
    before = _page_scope_before()
    before["items"] = [
        item for item in before["items"] if item["id"] not in {"child", "grandchild"}
    ]
    before["page_xml"] = {
        page_id: xml
        for page_id, xml in before["page_xml"].items()
        if page_id not in {"child", "grandchild"}
    }
    target = next(item for item in before["items"] if item["id"] == "selected")
    target["modified"] = "modified"
    after_items = [dict(item) for item in before["items"] if item["id"] != "selected"]
    after_items.append(
        {
            **target,
            "id": "selected-new",
            "parent_id": "destination-section",
            "section_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
            "order": 1,
        }
    )
    after = {
        "items": after_items,
        "page_xml": {
            **{
                page_id: xml
                for page_id, xml in before["page_xml"].items()
                if page_id != "selected"
            },
            "selected-new": _remap_xml(
                before["page_xml"]["selected"], "selected", "selected-new"
            ),
        },
    }
    snapshots = iter([before, after])
    calls: list[str] = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy, "resources", lambda **_kwargs: before["items"]
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: after_items,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: next(snapshots),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page-scope />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {},
    )

    arguments = [
        "selected",
        "destination-section",
        "Selected",
        "source-section",
        "modified",
    ]
    if explicit_false:
        arguments.append(False)
    result = server.services.mutations.reparent_page(*arguments)

    assert calls == ["update_hierarchy"]
    assert result["include_descendants"] is False
    assert result["preserved_descendants"] == {
        "promoted": False,
        "preserved_descendant_ids": [],
    }
    assert_destination_position_contract(result, after_items, "selected-new")


@pytest.mark.write_contract
@pytest.mark.parametrize("resource_type", ["section", "section_group", "page"])
def test_reparent_full_evidence_capture_may_exceed_convergence_deadline(
    monkeypatch,
    resource_type: str,
) -> None:
    items = _container_items()
    items.append(
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Destination Section",
            "parent_id": "destination-group-id",
            "notebook_id": "notebook-id",
            "is_in_recycle_bin": False,
        }
    )
    if resource_type == "section":
        target_id = "section-id"
        destination_id = "destination-group-id"
        expected_name = "Section"
        source_parent_id = "source-group-id"
        invoke = server.services.mutations.reparent_section
    elif resource_type == "section_group":
        target_id = "target-group-id"
        destination_id = "destination-group-id"
        expected_name = "Target Group"
        source_parent_id = "source-group-id"
        items.append(
            {
                "resource_type": "section_group",
                "id": target_id,
                "name": expected_name,
                "parent_id": source_parent_id,
                "notebook_id": "notebook-id",
                "modified": "modified",
                "is_in_recycle_bin": False,
            }
        )
        invoke = server.services.mutations.reparent_section_group
    else:
        target_id = "page-id"
        destination_id = "destination-section"
        expected_name = "Page"
        source_parent_id = "section-id"
        items.append(
            {
                "resource_type": "page",
                "id": target_id,
                "title": expected_name,
                "parent_id": source_parent_id,
                "section_id": source_parent_id,
                "notebook_id": "notebook-id",
                "page_level": 1,
                "parent_page_id": None,
                "order": 0,
                "modified": "modified",
                "is_in_recycle_bin": False,
            }
        )
        invoke = server.services.mutations.reparent_page

    target = next(item for item in items if item["id"] == target_id)
    target["modified"] = "modified"
    moved = dict(target)
    if resource_type == "page":
        moved.update(parent_id=destination_id, section_id=destination_id)
    else:
        moved["parent_id"] = destination_id
    after_items = [dict(item) for item in items if item["id"] != target_id]
    after_items.append(moved)
    before = {"items": items, "page_xml": {}}
    after = {"items": after_items, "page_xml": {}}
    now = [0.0]
    captures = 0
    calls: list[str] = []

    def capture(_notebook_id: str) -> dict:
        nonlocal captures
        captures += 1
        if captures == 1:
            return before
        now[0] += 5.0
        return after

    def capture_topology(_notebook_id: str) -> list[dict]:
        now[0] += 0.1
        return after_items

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda **_kwargs: items)
    monkeypatch.setattr(server.services.mutations, "_capture_reparent_snapshot", capture)
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        capture_topology,
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_xml",
        lambda *_args, **_kwargs: "<typed-container />",
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_page_scope_xml",
        lambda *_args, **_kwargs: "<typed-page />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_validate_reparent_snapshots",
        lambda *_args, **_kwargs: (moved, {target_id: target_id}, {"verified": True}),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_validate_reparent_page_scope",
        lambda *_args, **_kwargs: (moved, {target_id: target_id}, {"verified": True}),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {},
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.monotonic", lambda: now[0]
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    result = invoke(
        target_id,
        destination_id,
        expected_name,
        source_parent_id,
        "modified",
    )

    assert result["item"]["id"] == target_id
    assert result["convergence"]["stable_observations"] == 2
    assert now[0] > 4.0
    assert captures == 2
    assert calls == ["update_hierarchy"]


@pytest.mark.write_contract
def test_reparent_retries_full_capture_once_without_replaying_mutation(monkeypatch) -> None:
    before_items = _container_items()
    moved = {
        **next(item for item in before_items if item["id"] == "section-id"),
        "parent_id": "destination-group-id",
    }
    after_items = [
        dict(item) for item in before_items if item["id"] != "section-id"
    ] + [moved]
    before = {"items": before_items, "page_xml": {}}
    after = {"items": after_items, "page_xml": {}}
    captures = 0
    calls: list[str] = []

    def capture(_notebook_id: str) -> dict:
        nonlocal captures
        captures += 1
        if captures == 1:
            return before
        if captures == 2:
            raise RuntimeError("hierarchy changed during evidence capture")
        return after

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy, "resources", lambda **_kwargs: before_items
    )
    monkeypatch.setattr(server.services.mutations, "_capture_reparent_snapshot", capture)
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: after_items,
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_xml",
        lambda *_args, **_kwargs: "<typed-container />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {},
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.sleep", lambda _seconds: None
    )

    result = server.services.mutations.reparent_section(
        "section-id",
        "destination-group-id",
        "Section",
        "source-group-id",
        "modified",
    )

    assert result["item"]["parent_id"] == "destination-group-id"
    assert captures == 3
    assert calls == ["update_hierarchy"]


def test_reparent_snapshot_rejects_same_ids_with_changed_sibling_order(monkeypatch) -> None:
    initial = _container_items()
    refreshed = [initial[0], initial[2], initial[1], initial[3]]
    observations = iter([initial, refreshed])
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_hierarchy",
        lambda _notebook_id: next(observations),
    )

    with pytest.raises(RuntimeError, match="hierarchy changed"):
        server.services.mutations._capture_reparent_snapshot("notebook-id")


@pytest.mark.write_contract
def test_reparent_hierarchy_deadline_reports_stable_count_instead_of_none(
    monkeypatch,
) -> None:
    before_items = _container_items()
    moved = {
        **next(item for item in before_items if item["id"] == "section-id"),
        "parent_id": "destination-group-id",
    }
    after_items = [
        dict(item) for item in before_items if item["id"] != "section-id"
    ] + [moved]
    now = [0.0]
    calls: list[str] = []

    def slow_topology(_notebook_id: str) -> list[dict]:
        now[0] += 5.0
        return after_items

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", "true")
    monkeypatch.setattr(
        server.services.hierarchy, "resources", lambda **_kwargs: before_items
    )
    monkeypatch.setattr(
        server.services.mutations,
        "_capture_reparent_snapshot",
        lambda _notebook_id: {"items": before_items, "page_xml": {}},
    )
    monkeypatch.setattr(
        server.services.mutations, "_capture_reparent_hierarchy", slow_topology
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "reparent_xml",
        lambda *_args, **_kwargs: "<typed-container />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {},
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.monotonic", lambda: now[0]
    )

    with pytest.raises(PartialFailure, match="1/2 stable observations") as caught:
        server.services.mutations.reparent_section(
            "section-id",
            "destination-group-id",
            "Section",
            "source-group-id",
            "modified",
        )

    assert caught.value.details["readback_phase"] == "hierarchy_convergence"
    assert caught.value.details["readback_error"] == (
        "deadline exceeded after 1/2 stable observations"
    )
    assert calls == ["update_hierarchy"]
