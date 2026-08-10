"""Contracts for same-Notebook Section reparenting."""

from __future__ import annotations

import pytest

from local_onenote_mcp import server


@pytest.mark.write_contract
def test_reparent_section_rejects_cross_notebook_destination_before_com(monkeypatch) -> None:
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Source",
        "parent_id": "source-group-id",
        "notebook_id": "source-notebook-id",
        "modified": "source-modified",
    }
    destination = {
        "resource_type": "section_group",
        "id": "destination-group-id",
        "name": "Destination",
        "parent_id": "destination-notebook-id",
        "notebook_id": "destination-notebook-id",
        "modified": "destination-modified",
    }
    resources = {section["id"]: section, destination["id"]: destination}

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT_SECTION", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: resources[object_id],
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-Notebook rejection must happen before UpdateHierarchy")
        ),
    )

    with pytest.raises(
        ValueError,
        match="reparent_section only supports destinations in the same notebook",
    ):
        server.services.mutations.reparent_section(
            section["id"],
            destination["id"],
            section["name"],
            section["parent_id"],
            section["modified"],
        )
