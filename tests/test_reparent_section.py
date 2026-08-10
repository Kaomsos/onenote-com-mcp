"""Contracts for typed same-Notebook Reparent tools."""

from __future__ import annotations

import pytest

from local_onenote_mcp import server


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

    with pytest.raises(RuntimeError, match="requested parent was not observed"):
        server.services.mutations.reparent_section(
            "section-id", "destination-group-id", "Section", "source-group-id", "modified"
        )
    assert calls == ["update_hierarchy"]
