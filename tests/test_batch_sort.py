from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import operation_catalog, server
from local_onenote_mcp.services.errors import MutationPreflightFailure, PartialFailure
from local_onenote_mcp.tools.responses import caught as caught_response


def item(
    resource_type: str,
    object_id: str,
    parent_id: str | None,
    name: str,
    *,
    notebook_id: str = "n",
    section_id: str | None = None,
    order: int | None = None,
    level: int = 1,
    parent_page_id: str | None = None,
    created: str = "2026-08-01T00:00:00Z",
    modified: str = "2026-08-02T00:00:00Z",
) -> dict:
    value = {
        "resource_type": resource_type,
        "id": object_id,
        "name": name,
        "parent_id": parent_id,
        "notebook_id": None if resource_type == "notebook" else notebook_id,
        "created": created,
        "modified": modified,
        "is_in_recycle_bin": False,
    }
    if resource_type == "page":
        value.update(
            title=name,
            section_id=section_id,
            order=order,
            page_level=level,
            parent_page_id=parent_page_id,
        )
    return value


def install_snapshot(monkeypatch, state):
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: copy.deepcopy(state["items"]),
    )


def page_confirmation(value: dict, *, new_title: str | None = None) -> dict:
    result = {
        "page_id": value["id"],
        "expected_title": value["title"],
        "expected_section_id": value["section_id"],
        "expected_modified": value["modified"],
    }
    if new_title is not None:
        result["new_title"] = new_title
    return result


def container_confirmation(value: dict, *, new_name: str | None = None) -> dict:
    id_key = (
        "section_id"
        if value["resource_type"] == "section"
        else "section_group_id"
    )
    result = {
        id_key: value["id"],
        "expected_name": value["name"],
        "expected_parent_id": value["parent_id"],
        "expected_modified": value["modified"],
    }
    if new_name is not None:
        result["new_name"] = new_name
    return result


def assert_batch_partial_contract(
    failure: PartialFailure,
    operation: str,
) -> None:
    details = failure.details
    assert details["partial"] is True
    assert details["operation"] == operation
    assert details["applied_count"] == 1
    assert details["failed_index"] == 1
    assert [entry["input_index"] for entry in details["items"]] == [0, 1, 2]
    assert [entry["status"] for entry in details["items"]] == [
        "applied",
        "failed",
        "not_attempted",
    ]
    assert details["manual_recovery_required"] is True
    assert details["retryability"] == "inspect_live_state_before_new_call"
    assert details["rollback_attempted"] is False
    assert details["mutation_replayed"] is False
    envelope = caught_response(failure, execution={"replayed": False})
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "partial_failure"
    assert envelope["error"]["details"]["items"] == details["items"]
    assert envelope["error"]["details"]["rollback_attempted"] is False
    assert envelope["error"]["details"]["mutation_replayed"] is False
    assert envelope["execution"] == {"replayed": False}


@pytest.mark.parametrize(
    ("operation", "enabled"),
    [
        ("rename", {}),
        ("reparent", {"LOCAL_ONENOTE_ENABLE_WRITES": "true"}),
        ("delete", {}),
        ("create_section", {}),
        ("create_page", {"LOCAL_ONENOTE_ENABLE_CREATE": "true"}),
        ("sort", {}),
    ],
)
def test_batch_and_sort_gates_reject_before_hierarchy_read(
    monkeypatch, operation, enabled
):
    for key, value in enabled.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("policy rejection must precede hierarchy read")
        ),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("policy rejection must precede hierarchy read")
        ),
    )

    with pytest.raises(PermissionError):
        if operation == "rename":
            server.services.mutations.batch_rename("page", [{}])
        elif operation == "reparent":
            server.services.mutations.batch_reparent("page", "s", [{}])
        elif operation == "delete":
            server.services.mutations.batch_delete("page", [{}])
        elif operation == "create_section":
            server.services.mutations.batch_create(
                "section", "n", "Notebook", None, [{}]
            )
        elif operation == "create_page":
            server.services.mutations.batch_create(
                "page", "s", "Section", None, [{}]
            )
        else:
            server.services.mutations.sort_children(
                None, "n", "Notebook", None, ["s"], "name", "ascending"
            )


@pytest.mark.write_contract
def test_batch_rename_preflights_all_then_stops_and_reports_partial_outcome(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    pages = [
        item("page", f"p{index}", "s", f"Old {index}", section_id="s", order=index)
        for index in range(3)
    ]
    state = {
        "items": [
            item("notebook", "n", None, "Notebook"),
            item("section", "s", "n", "Section"),
            *pages,
        ]
    }
    install_snapshot(monkeypatch, state)
    calls = []

    def rename(page_id, *_args):
        calls.append(page_id)
        if page_id == "p1":
            raise RuntimeError("uncertain backend outcome")
        return {"item": {"id": page_id}}

    monkeypatch.setattr(server.services.mutations, "update_page_title", rename)
    supplied = [
        page_confirmation(value, new_title=f"New {index}")
        for index, value in enumerate(pages)
    ]

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.batch_rename("page", supplied)

    assert calls == ["p0", "p1"]
    details = caught.value.details
    assert details["applied_count"] == 1
    assert [entry["status"] for entry in details["items"]] == [
        "applied",
        "failed",
        "not_attempted",
    ]
    assert details["items"][2]["object_id"] == "p2"
    assert_batch_partial_contract(caught.value, "rename_page")


@pytest.mark.parametrize("resource_type", ["page", "section", "section_group"])
def test_batch_rename_supports_explicit_typed_mappings_for_each_type(
    monkeypatch, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    if resource_type == "page":
        parent = item("section", "s", "n", "Section")
        targets = [
            item("page", f"p{i}", "s", f"Old {i}", section_id="s", order=i)
            for i in range(2)
        ]
        supplied = [
            page_confirmation(value, new_title=f"New {i}")
            for i, value in enumerate(targets)
        ]
    else:
        parent = None
        targets = [item(resource_type, f"x{i}", "n", f"Old {i}") for i in range(2)]
        supplied = [
            container_confirmation(value, new_name=f"New {i}")
            for i, value in enumerate(targets)
        ]
    install_snapshot(
        monkeypatch,
        {"items": [notebook, *([] if parent is None else [parent]), *targets]},
    )
    calls = []
    if resource_type == "page":
        monkeypatch.setattr(
            server.services.mutations,
            "update_page_title",
            lambda page_id, title, *_args: calls.append((page_id, title))
            or {"item": {"id": page_id, "title": title}},
        )
    else:
        monkeypatch.setattr(
            server.services.mutations,
            "rename_resource",
            lambda object_id, supplied_type, new_name, *_args: calls.append(
                (object_id, supplied_type, new_name)
            )
            or {"item": {"id": object_id, "name": new_name}},
        )

    result = server.services.mutations.batch_rename(resource_type, supplied)

    assert result["applied_count"] == 2
    if resource_type == "page":
        assert calls == [("p0", "New 0"), ("p1", "New 1")]
    else:
        assert calls == [
            ("x0", resource_type, "New 0"),
            ("x1", resource_type, "New 1"),
        ]


def test_batch_rename_rejects_noop_duplicates_collision_exchange_and_cycle_before_rename(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    sections = [
        item("section", "a", "n", "Alpha"),
        item("section", "b", "n", "Beta"),
        item("section", "c", "n", "Gamma"),
        item("section", "d", "n", "Existing"),
    ]
    install_snapshot(monkeypatch, {"items": [notebook, *sections]})
    monkeypatch.setattr(
        server.services.mutations,
        "rename_resource",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not rename")),
    )

    with pytest.raises(MutationPreflightFailure, match="no-op"):
        server.services.mutations.batch_rename(
            "section", [container_confirmation(sections[0], new_name="Alpha")]
        )
    with pytest.raises(MutationPreflightFailure, match="duplicate sibling names"):
        server.services.mutations.batch_rename(
            "section",
            [
                container_confirmation(sections[0], new_name="Same"),
                container_confirmation(sections[1], new_name=" same "),
            ],
        )
    with pytest.raises(MutationPreflightFailure, match="existing sibling collision"):
        server.services.mutations.batch_rename(
            "section", [container_confirmation(sections[0], new_name="Existing")]
        )
    with pytest.raises(MutationPreflightFailure, match="name exchange/cycle"):
        server.services.mutations.batch_rename(
            "section",
            [
                container_confirmation(sections[0], new_name="Beta"),
                container_confirmation(sections[1], new_name="Alpha"),
            ],
        )
    with pytest.raises(MutationPreflightFailure, match="name exchange/cycle"):
        server.services.mutations.batch_rename(
            "section",
            [
                container_confirmation(sections[0], new_name="Beta"),
                container_confirmation(sections[1], new_name="Gamma"),
                container_confirmation(sections[2], new_name="Alpha"),
            ],
        )


def test_batch_rename_confirmation_and_cross_notebook_fail_before_rename(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebooks = [item("notebook", "n1", None, "N1"), item("notebook", "n2", None, "N2")]
    sections = [
        item("section", "s1", "n1", "First", notebook_id="n1"),
        item("section", "s2", "n2", "Second", notebook_id="n2"),
    ]
    install_snapshot(monkeypatch, {"items": [*notebooks, *sections]})
    monkeypatch.setattr(
        server.services.mutations,
        "rename_resource",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not rename")),
    )
    mismatched = container_confirmation(sections[0], new_name="New")
    mismatched["expected_parent_id"] = "changed"
    with pytest.raises(MutationPreflightFailure, match="parent changed"):
        server.services.mutations.batch_rename("section", [mismatched])
    with pytest.raises(MutationPreflightFailure, match="one active Notebook"):
        server.services.mutations.batch_rename(
            "section",
            [
                container_confirmation(sections[0], new_name="New First"),
                container_confirmation(sections[1], new_name="New Second"),
            ],
        )


@pytest.mark.write_contract
def test_batch_preflight_rejects_duplicate_and_overlapping_page_targets(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "root", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    state = {
        "items": [
            item("notebook", "n", None, "Notebook"),
            item("section", "s", "n", "Section"),
            root,
            child,
        ]
    }
    install_snapshot(monkeypatch, state)

    with pytest.raises(MutationPreflightFailure, match="unique"):
        server.services.mutations.batch_delete(
            "page", [page_confirmation(root), page_confirmation(root)]
        )
    with pytest.raises(MutationPreflightFailure, match="ancestor/descendant"):
        server.services.mutations.batch_delete(
            "page", [page_confirmation(root), page_confirmation(child)]
        )


@pytest.mark.parametrize("resource_type", ["page", "section", "section_group"])
def test_batch_reparent_preflights_once_and_moves_same_notebook_items_to_one_parent(
    monkeypatch, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", "true")
    notebook = item("notebook", "n", None, "Notebook")
    if resource_type == "page":
        source = item("section", "source", "n", "Source")
        destination = item("section", "destination", "n", "Destination")
        targets = [
            item("page", f"p{i}", "source", f"Page {i}", section_id="source", order=i)
            for i in range(2)
        ]
        supplied = [page_confirmation(value) for value in targets]
    elif resource_type == "section":
        destination = item("section_group", "destination", "n", "Destination")
        targets = [item("section", f"s{i}", "n", f"Section {i}") for i in range(2)]
        supplied = [container_confirmation(value) for value in targets]
        source = None
    else:
        destination = item("section_group", "destination", "n", "Destination")
        targets = [
            item("section_group", f"g{i}", "n", f"Group {i}") for i in range(2)
        ]
        supplied = [container_confirmation(value) for value in targets]
        source = None
    state = {
        "items": [notebook, *([] if source is None else [source]), destination, *targets]
    }
    snapshots = []

    def resources(include_recycle_bin=False):
        snapshots.append(include_recycle_bin)
        return copy.deepcopy(state["items"])

    monkeypatch.setattr(server.services.hierarchy, "resources", resources)
    calls = []
    if resource_type == "page":
        def reparent_page(page_id, destination_id, *_args):
            calls.append((page_id, destination_id))
            new_id = f"new-{page_id}"
            original = next(value for value in state["items"] if value.get("id") == page_id)
            state["items"] = [
                value for value in state["items"] if value.get("id") != page_id
            ]
            state["items"].append(
                {
                    **original,
                    "id": new_id,
                    "parent_id": destination_id,
                    "section_id": destination_id,
                }
            )
            return {"id_map": {page_id: new_id}, "item": {"id": new_id}}

        monkeypatch.setattr(server.services.mutations, "reparent_page", reparent_page)
    else:
        method_name = f"reparent_{resource_type}"

        def reparent_container(object_id, destination_id, *_args):
            calls.append((object_id, destination_id))
            for value in state["items"]:
                if value.get("id") == object_id:
                    value["parent_id"] = destination_id
            return {"item": {"id": object_id, "parent_id": destination_id}}

        monkeypatch.setattr(server.services.mutations, method_name, reparent_container)

    result = server.services.mutations.batch_reparent(
        resource_type, destination["id"], supplied
    )

    assert snapshots == [True, True]
    assert calls == [(value["id"], "destination") for value in targets]
    assert result["mode"] == "batch"
    assert result["applied_count"] == 2
    assert [entry["input_index"] for entry in result["items"]] == [0, 1]
    assert result["final_hierarchy"]["item_count"] == 2
    assert result["final_hierarchy"]["verification_scope"] == {
        "page_content": "not_read"
    }
    if resource_type == "page":
        assert result["items"][0]["result"]["id_map"] == {"p0": "new-p0"}


def test_batch_reparent_rejects_cross_notebook_cycle_overlap_and_oversize_before_move(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", "true")
    notebooks = [
        item("notebook", "n1", None, "N1", notebook_id="n1"),
        item("notebook", "n2", None, "N2", notebook_id="n2"),
    ]
    sections = [
        item("section", "s1", "n1", "S1", notebook_id="n1"),
        item("section", "s2", "n2", "S2", notebook_id="n2"),
    ]
    destination = item("section_group", "dest", "n1", "Dest", notebook_id="n1")
    state = {"items": [*notebooks, *sections, destination]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.mutations,
        "reparent_section",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not move")),
    )
    with pytest.raises(MutationPreflightFailure, match="one active Notebook"):
        server.services.mutations.batch_reparent(
            "section",
            "dest",
            [container_confirmation(value) for value in sections],
        )

    parent = item("section_group", "parent", "n1", "Parent", notebook_id="n1")
    child = item(
        "section_group", "child", "parent", "Child", notebook_id="n1"
    )
    state["items"] = [notebooks[0], parent, child]
    with pytest.raises(MutationPreflightFailure, match="selected target or its descendant"):
        server.services.mutations.batch_reparent(
            "section_group", "child", [container_confirmation(parent)]
        )

    section = item("section", "pages", "n1", "Pages", notebook_id="n1")
    root = item(
        "page", "root", "pages", "Root", notebook_id="n1", section_id="pages", order=0
    )
    child_page = item(
        "page", "child-page", "pages", "Child", notebook_id="n1",
        section_id="pages", order=1, level=2, parent_page_id="root"
    )
    destination_section = item(
        "section", "page-dest", "n1", "Page Dest", notebook_id="n1"
    )
    state["items"] = [notebooks[0], section, destination_section, root, child_page]
    with pytest.raises(MutationPreflightFailure, match="ancestor/descendant"):
        server.services.mutations.batch_reparent(
            "page",
            "page-dest",
            [
                {**page_confirmation(root), "page_scope": "indentation_subtree"},
                page_confirmation(child_page),
            ],
        )

    too_many = [
        {"page_id": f"p{i}", "expected_title": "P", "expected_section_id": "pages"}
        for i in range(21)
    ]
    with pytest.raises(MutationPreflightFailure, match="between 1 and 20"):
        server.services.mutations.batch_reparent("page", "page-dest", too_many)


def test_batch_reparent_stops_on_item_failure_and_final_snapshot_failure_is_partial(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", "true")
    notebook = item("notebook", "n", None, "Notebook")
    destination = item("section_group", "dest", "n", "Destination")
    targets = [item("section", f"s{i}", "n", f"Section {i}") for i in range(3)]
    state = {"items": [notebook, destination, *targets]}
    install_snapshot(monkeypatch, state)
    calls = []

    def fail_second(section_id, *_args):
        calls.append(section_id)
        if section_id == "s1":
            raise RuntimeError("uncertain")
        return {"item": {"id": section_id}}

    monkeypatch.setattr(server.services.mutations, "reparent_section", fail_second)
    with pytest.raises(PartialFailure) as stopped:
        server.services.mutations.batch_reparent(
            "section",
            "dest",
            [container_confirmation(value) for value in targets],
        )
    assert calls == ["s0", "s1"]
    assert [entry["status"] for entry in stopped.value.details["items"]] == [
        "applied", "failed", "not_attempted"
    ]
    assert_batch_partial_contract(stopped.value, "reparent_section")

    calls.clear()
    monkeypatch.setattr(
        server.services.mutations,
        "reparent_section",
        lambda section_id, *_args: calls.append(section_id)
        or {"item": {"id": section_id}},
    )
    with pytest.raises(PartialFailure) as final_failure:
        server.services.mutations.batch_reparent(
            "section",
            "dest",
            [container_confirmation(value) for value in targets[:2]],
        )
    assert calls == ["s0", "s1"]
    assert final_failure.value.details["failed_step"] == "batch_final_hierarchy"
    assert final_failure.value.details["applied_count"] == 2


@pytest.mark.parametrize("resource_type", ["page", "section", "section_group"])
def test_batch_delete_supports_each_recoverable_type_and_never_requests_permanent(
    monkeypatch, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    if resource_type == "page":
        parent = item("section", "s", "n", "Section")
        targets = [
            item("page", f"p{i}", "s", f"Page {i}", section_id="s", order=i)
            for i in range(2)
        ]
        supplied = [page_confirmation(value) for value in targets]
    else:
        parent = None
        targets = [
            item(resource_type, f"x{i}", "n", f"Target {i}") for i in range(2)
        ]
        supplied = [container_confirmation(value) for value in targets]
    install_snapshot(
        monkeypatch,
        {"items": [notebook, *([] if parent is None else [parent]), *targets]},
    )
    calls = []
    if resource_type == "page":
        monkeypatch.setattr(
            server.services.mutations,
            "delete_page",
            lambda page_id, *_args: calls.append((page_id, _args[-1]))
            or {"object_id": page_id, "permanently": _args[-1], "deleted": True},
        )
    else:
        monkeypatch.setattr(
            server.services.mutations,
            "delete_resource",
            lambda object_id, supplied_type, *_args: calls.append(
                (object_id, supplied_type, _args[-1])
            )
            or {"object_id": object_id, "permanently": _args[-1], "deleted": True},
        )

    result = server.services.mutations.batch_delete(resource_type, supplied)

    assert result["applied_count"] == 2
    assert all(entry["result"]["permanently"] is False for entry in result["items"])
    if resource_type == "page":
        assert calls == [("p0", False), ("p1", False)]
    else:
        assert calls == [("x0", resource_type, False), ("x1", resource_type, False)]


def test_batch_delete_confirmation_recycle_and_cross_notebook_fail_before_delete(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook1 = item("notebook", "n1", None, "N1")
    notebook2 = item("notebook", "n2", None, "N2")
    first = item("section", "s1", "n1", "First", notebook_id="n1")
    second = item("section", "s2", "n2", "Second", notebook_id="n2")
    state = {"items": [notebook1, notebook2, first, second]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not delete")),
    )
    mismatched = container_confirmation(first)
    mismatched["expected_name"] = "Changed"
    with pytest.raises(MutationPreflightFailure, match="display name changed"):
        server.services.mutations.batch_delete("section", [mismatched])

    recycled = {**first, "is_in_recycle_bin": True}
    state["items"] = [notebook1, recycled]
    with pytest.raises(MutationPreflightFailure, match="recycle bin"):
        server.services.mutations.batch_delete(
            "section", [container_confirmation(recycled)]
        )

    state["items"] = [notebook1, notebook2, first, second]
    with pytest.raises(MutationPreflightFailure, match="one active Notebook"):
        server.services.mutations.batch_delete(
            "section", [container_confirmation(first), container_confirmation(second)]
        )


def test_batch_delete_stops_after_uncertain_item_and_reports_recovery(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    targets = [item("section", f"s{i}", "n", f"Section {i}") for i in range(3)]
    install_snapshot(monkeypatch, {"items": [notebook, *targets]})
    calls = []

    def delete_resource(object_id, *_args):
        calls.append(object_id)
        if object_id == "s1":
            raise RuntimeError("uncertain")
        return {"object_id": object_id, "permanently": False}

    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_resource)
    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.batch_delete(
            "section", [container_confirmation(value) for value in targets]
        )

    assert calls == ["s0", "s1"]
    assert caught.value.details["manual_recovery_required"] is True
    assert [entry["status"] for entry in caught.value.details["items"]] == [
        "applied",
        "failed",
        "not_attempted",
    ]
    assert_batch_partial_contract(caught.value, "delete_section")


@pytest.mark.write_contract
def test_batch_create_rejects_normalized_duplicate_before_any_create(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    parent = item("notebook", "n", None, "Notebook")
    state = {"items": [parent]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.mutations,
        "create_section",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not create")),
    )

    with pytest.raises(MutationPreflightFailure, match="unique"):
        server.services.mutations.batch_create(
            "section",
            "n",
            "Notebook",
            parent["modified"],
            [{"name": "Same"}, {"name": " Same.one "}],
        )


@pytest.mark.write_contract
def test_batch_create_page_preserves_duplicate_title_semantics(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    section = item("section", "s", "n", "Section")
    state = {
        "items": [item("notebook", "n", None, "Notebook"), section]
    }
    install_snapshot(monkeypatch, state)
    calls = []

    def create_page(section_id, title, *_args):
        calls.append((section_id, title))
        allocated_id = f"p{len(calls)}"
        return {
            "page": {"id": allocated_id, "title": title},
            "allocated_id": allocated_id,
        }

    monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    result = server.services.mutations.batch_create(
        "page",
        "s",
        "Section",
        section["modified"],
        [{"title": "Same"}, {"title": "Same"}],
    )

    assert result["applied_count"] == 2
    assert calls == [("s", "Same"), ("s", "Same")]
    assert [entry["result"]["allocated_id"] for entry in result["items"]] == [
        "p1", "p2"
    ]


@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_batch_create_container_returns_each_allocated_identity(monkeypatch, resource_type):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    parent = item("notebook", "n", None, "Notebook")
    install_snapshot(monkeypatch, {"items": [parent]})
    calls = []
    method_name = "create_section" if resource_type == "section" else "create_section_group"

    def create(parent_id, name):
        calls.append((parent_id, name))
        return {
            "item": {"id": f"allocated-{len(calls)}", "name": name},
            "allocated_id": f"allocated-{len(calls)}",
        }

    monkeypatch.setattr(server.services.mutations, method_name, create)
    result = server.services.mutations.batch_create(
        resource_type,
        "n",
        "Notebook",
        parent["modified"],
        [{"name": "First"}, {"name": "Second"}],
    )

    assert calls == [("n", "First"), ("n", "Second")]
    assert [entry["result"]["allocated_id"] for entry in result["items"]] == [
        "allocated-1",
        "allocated-2",
    ]


def test_batch_create_rejects_parent_confirmation_type_collision_and_budget_before_create(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    existing = item("section", "existing", "n", "Existing")
    state = {"items": [notebook, section, existing]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.mutations,
        "create_section",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    with pytest.raises(MutationPreflightFailure, match="confirmation changed"):
        server.services.mutations.batch_create(
            "section", "n", "Changed", notebook["modified"], [{"name": "New"}]
        )
    with pytest.raises(MutationPreflightFailure, match="Container batch parent"):
        server.services.mutations.batch_create(
            "section", "s", "Section", section["modified"], [{"name": "New"}]
        )
    with pytest.raises(MutationPreflightFailure, match="collides"):
        server.services.mutations.batch_create(
            "section", "n", "Notebook", notebook["modified"], [{"name": "Existing"}]
        )

    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.CopyBudget.current",
        lambda: type("Budget", (), {"max_resources": 3, "max_pages": 200})(),
    )
    with pytest.raises(MutationPreflightFailure, match="resource budget"):
        server.services.mutations.batch_create(
            "section",
            "n",
            "Notebook",
            notebook["modified"],
            [{"name": "One"}, {"name": "Two"}],
        )

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.CopyBudget.current",
        lambda: type("Budget", (), {"max_resources": 100, "max_pages": 100})(),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    with pytest.raises(MutationPreflightFailure, match="500000-character"):
        server.services.mutations.batch_create(
            "page",
            "s",
            "Section",
            section["modified"],
            [{"title": f"P{i}", "content": "x" * 100_000} for i in range(6)],
        )


def test_batch_create_stops_after_failure_and_preserves_input_index_handoff(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    parent = item("notebook", "n", None, "Notebook")
    install_snapshot(monkeypatch, {"items": [parent]})
    calls = []

    def create_section(_parent_id, name):
        calls.append(name)
        if name == "Second":
            raise RuntimeError("uncertain")
        return {"allocated_id": f"id-{name}"}

    monkeypatch.setattr(server.services.mutations, "create_section", create_section)
    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.batch_create(
            "section",
            "n",
            "Notebook",
            parent["modified"],
            [{"name": "First"}, {"name": "Second"}, {"name": "Third"}],
        )

    assert calls == ["First", "Second"]
    assert caught.value.details["applied_count"] == 1
    assert [entry["input_index"] for entry in caught.value.details["items"]] == [
        0,
        1,
        2,
    ]
    assert_batch_partial_contract(caught.value, "create_section")


@pytest.mark.write_contract
def test_sort_sections_is_stable_and_preserves_section_group_slots_and_content_free_verification(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    before = [
        item("notebook", "n", None, "Notebook"),
        item("section_group", "g", "n", "Group"),
        item("section", "sB", "n", "Beta"),
        item("page", "pB", "sB", "Page B", section_id="sB", order=0),
        item("section", "sA", "n", "Alpha"),
        item("page", "pA", "sA", "Page A", section_id="sA", order=0),
    ]
    after = [before[0], before[1], before[4], before[5], before[2], before[3]]
    state = {"items": before, "xml": "", "calls": 0}
    install_snapshot(monkeypatch, state)

    def call(operation, **params):
        assert operation == "update_hierarchy"
        state["calls"] += 1
        state["xml"] = params["xml"]
        state["items"] = after
        return {"updated": True}

    monkeypatch.setattr(server.services.mutations, "call", call)
    result = server.services.mutations.sort_sections(
        "n", "Notebook", before[0]["modified"], ["sB", "sA"], "name", "ascending"
    )

    assert result["child_ids"] == ["sA", "sB"]
    assert result["verification_scope"] == {"page_content": "not_read"}
    assert state["calls"] == 1
    root = ET.fromstring(state["xml"])
    notebook = next(node for node in root.iter() if node.attrib.get("ID") == "n")
    assert [node.attrib["ID"] for node in notebook] == ["g", "sA", "sB"]


@pytest.mark.write_contract
def test_sort_sections_fails_closed_if_backend_moves_section_group_slot(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    group = item("section_group", "g", "n", "Group")
    beta = item("section", "b", "n", "Beta")
    alpha = item("section", "a", "n", "Alpha")
    state = {"items": [notebook, group, beta, alpha]}
    install_snapshot(monkeypatch, state)

    def call(_operation, **_params):
        state["items"] = [notebook, alpha, group, beta]
        return {"updated": True}

    monkeypatch.setattr(server.services.mutations, "call", call)

    with pytest.raises(PartialFailure):
        server.services.mutations.sort_sections(
            "n", "Notebook", notebook["modified"], ["b", "a"], "name", "ascending"
        )


@pytest.mark.write_contract
def test_sort_pages_under_leveled_page_moves_complete_blocks_only(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    parent = item("page", "parent", "s", "Parent", section_id="s", order=0)
    beta = item("page", "beta", "parent", "Beta", section_id="s", order=1, level=2, parent_page_id="parent")
    beta_child = item("page", "beta-child", "beta", "Beta Child", section_id="s", order=2, level=3, parent_page_id="beta")
    alpha = item("page", "alpha", "parent", "Alpha", section_id="s", order=3, level=2, parent_page_id="parent")
    alpha_child = item("page", "alpha-child", "alpha", "Alpha Child", section_id="s", order=4, level=3, parent_page_id="alpha")
    outside = item("page", "outside", "s", "Outside", section_id="s", order=5)
    before = [notebook, section, parent, beta, beta_child, alpha, alpha_child, outside]
    reordered_pages = [parent, alpha, alpha_child, beta, beta_child, outside]
    after_pages = [{**value, "order": index} for index, value in enumerate(reordered_pages)]
    state = {"items": before, "xml": "", "calls": 0}
    install_snapshot(monkeypatch, state)

    def call(operation, **params):
        assert operation == "update_hierarchy"
        state["calls"] += 1
        state["xml"] = params["xml"]
        state["items"] = [notebook, section, *after_pages]
        return {"updated": True}

    monkeypatch.setattr(server.services.mutations, "call", call)
    result = server.services.mutations.sort_pages(
        "parent", "Parent", parent["modified"], ["beta", "alpha"], "name", "ascending"
    )

    assert result["child_ids"] == ["alpha", "beta"]
    assert [value["id"] for value in result["pages"]] == [
        "parent", "alpha", "alpha-child", "beta", "beta-child", "outside"
    ]
    root = ET.fromstring(state["xml"])
    section_node = next(node for node in root.iter() if node.attrib.get("ID") == "s")
    assert [node.attrib["ID"] for node in section_node] == [
        "parent", "alpha", "alpha-child", "beta", "beta-child", "outside"
    ]


@pytest.mark.write_contract
def test_sort_time_key_missing_fails_closed_before_update(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    first = item("section", "a", "n", "A")
    second = item("section", "b", "n", "B")
    second["created"] = None
    state = {"items": [notebook, first, second]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not update")),
    )

    with pytest.raises(MutationPreflightFailure, match="missing"):
        server.services.mutations.sort_sections(
            "n", "Notebook", notebook["modified"], ["a", "b"], "created", "ascending"
        )


@pytest.mark.parametrize("key", ["name", "created", "modified"])
@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("ascending", ["early-1", "early-2", "late"]),
        ("descending", ["late", "early-1", "early-2"]),
    ],
)
def test_sort_each_key_and_direction_is_stable_for_equal_values(
    key, direction, expected
):
    values = [
        item(
            "section", "late", "n", "Zulu",
            created="2026-08-02T00:00:00Z",
            modified="2026-08-04T00:00:00Z",
        ),
        item(
            "section", "early-1", "n", "Alpha",
            created="2026-08-01T00:00:00Z",
            modified="2026-08-03T00:00:00Z",
        ),
        item(
            "section", "early-2", "n", "Alpha",
            created="2026-08-01T00:00:00Z",
            modified="2026-08-03T00:00:00Z",
        ),
    ]

    ordered = server.services.mutations._ordered_for_sort(values, key, direction)

    assert [value["id"] for value in ordered] == expected


def test_sort_unparseable_time_and_concurrent_direct_child_change_fail_before_update(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    first = item("section", "a", "n", "A")
    second = item("section", "b", "n", "B")
    first["modified"] = "not-a-time"
    install_snapshot(monkeypatch, {"items": [notebook, first, second]})
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not update")
        ),
    )
    with pytest.raises(MutationPreflightFailure, match="not a comparable timestamp"):
        server.services.mutations.sort_sections(
            "n", "Notebook", notebook["modified"], ["a", "b"], "modified", "ascending"
        )
    with pytest.raises(MutationPreflightFailure, match="complete ordered direct-child"):
        server.services.mutations.sort_sections(
            "n", "Notebook", notebook["modified"], ["b", "a"], "name", "ascending"
        )


def test_sort_complete_direct_sequence_is_not_limited_by_batch_item_cap(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    sections = [
        item("section", f"s{i:02d}", "n", f"Section {i:02d}")
        for i in range(21)
    ]
    install_snapshot(monkeypatch, {"items": [notebook, *sections]})

    result = server.services.mutations.sort_sections(
        "n",
        "Notebook",
        notebook["modified"],
        [value["id"] for value in sections],
        "name",
        "ascending",
    )

    assert result["changed"] is False
    assert len(result["child_ids"]) == 21


@pytest.mark.write_contract
def test_sort_pages_under_section_moves_only_root_blocks_and_never_reads_page_body(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    beta = item("page", "beta", "s", "Beta", section_id="s", order=0)
    beta_child = item(
        "page", "beta-child", "s", "Beta Child", section_id="s", order=1,
        level=2, parent_page_id="beta"
    )
    alpha = item("page", "alpha", "s", "Alpha", section_id="s", order=2)
    alpha_child = item(
        "page", "alpha-child", "s", "Alpha Child", section_id="s", order=3,
        level=2, parent_page_id="alpha"
    )
    before = [notebook, section, beta, beta_child, alpha, alpha_child]
    reordered = [alpha, alpha_child, beta, beta_child]
    after_pages = [{**value, "order": index} for index, value in enumerate(reordered)]
    state = {"items": before}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sort must not read Page bodies")
        ),
    )

    def call(_operation, **_params):
        state["items"] = [notebook, section, *after_pages]
        return {"updated": True}

    monkeypatch.setattr(server.services.mutations, "call", call)
    result = server.services.mutations.sort_pages(
        "s", "Section", section["modified"], ["beta", "alpha"], "name", "ascending"
    )

    assert result["child_ids"] == ["alpha", "beta"]
    assert [value["id"] for value in result["pages"]] == [
        "alpha", "alpha-child", "beta", "beta-child"
    ]
    assert result["verification_scope"] == {"page_content": "not_read"}


def test_batch_and_sort_public_schemas_are_bounded_strict_and_nonrecursive():
    tools = server.mcp._tool_manager._tools
    assert not any(name.startswith("batch_") for name in tools)
    batch_tools = {
        "create_section_group",
        "create_section",
        "create_page",
        "rename_page",
        "rename_section_group",
        "rename_section",
        "reparent_page",
        "reparent_section_group",
        "reparent_section",
        "delete_page",
        "delete_section_group",
        "delete_section",
    }
    assert all("items" in tools[name].parameters["properties"] for name in batch_tools)
    for name in batch_tools:
        items_schema = tools[name].parameters["properties"]["items"]
        array = next(
            value for value in items_schema["anyOf"] if value.get("type") == "array"
        )
        assert (array["minItems"], array["maxItems"]) == (1, 20)
    batch = tools["rename_page"].parameters
    items = batch["properties"]["items"]
    array_schema = next(value for value in items["anyOf"] if value.get("type") == "array")
    assert array_schema["minItems"] == 1
    assert array_schema["maxItems"] == 20
    assert batch["$defs"]["PageRenameItem"]["additionalProperties"] is False
    section_item = tools["rename_section"].parameters["$defs"]["SectionRenameItem"]
    assert "section_id" in section_item["properties"]
    assert "object_id" not in section_item["properties"]
    group_item = tools["rename_section_group"].parameters["$defs"]["SectionGroupRenameItem"]
    assert "section_group_id" in group_item["properties"]
    assert "object_id" not in group_item["properties"]
    for name in {"delete_page", "delete_section", "delete_section_group"}:
        assert "permanently" not in tools[name].parameters["properties"]
    sort = tools["sort_children"].parameters
    assert set(sort["properties"]) == {
        "parent_id",
        "child_type",
        "expected_parent_name",
        "expected_child_ids",
        "key",
        "direction",
        "expected_parent_modified",
    }
    assert "recursive" not in sort["properties"]
    assert sort["properties"]["expected_child_ids"]["maxItems"] == 1000
    assert "section_group" not in str(sort["properties"]["child_type"])


def test_existing_public_name_dispatches_items_batch_mode(monkeypatch):
    supplied = [{"page_id": "p"}]
    calls = []
    monkeypatch.setattr(
        server.services.mutations,
        "batch_rename",
        lambda resource_type, items: calls.append((resource_type, items)) or {"mode": "batch"},
    )
    handler = operation_catalog.build_operation_registry(
        server.services
    ).bindings["rename_page"].handler

    result = handler({"items": supplied, "expected_modified": None})

    assert result == {"mode": "batch"}
    assert calls == [("page", supplied)]
    with pytest.raises(ValueError, match="mutually exclusive"):
        handler({
            "items": supplied,
            "page_id": "p",
            "expected_modified": None,
        })

    reparent_handler = operation_catalog.build_operation_registry(
        server.services
    ).bindings["reparent_page"].handler
    with pytest.raises(ValueError, match="non-default single-item fields"):
        reparent_handler({
            "items": supplied,
            "destination_section_id": "s",
            "page_scope": "indentation_subtree",
            "expected_modified": None,
        })


@pytest.mark.parametrize(
    ("tool_name", "family", "resource_type", "arguments"),
    [
        ("create_section_group", "create", "section_group", {
            "parent_id": "n", "expected_parent_name": "N",
            "expected_parent_modified": None, "items": [{"name": "G"}],
        }),
        ("create_section", "create", "section", {
            "parent_id": "n", "expected_parent_name": "N",
            "expected_parent_modified": None, "items": [{"name": "S"}],
        }),
        ("create_page", "create", "page", {
            "section_id": "s", "expected_section_name": "S",
            "expected_section_modified": None, "content": "",
            "content_format": "plain", "items": [{"title": "P"}],
        }),
        ("rename_page", "rename", "page", {"items": [{"page_id": "p"}]}),
        ("rename_section", "rename", "section", {"items": [{"section_id": "s"}]}),
        ("rename_section_group", "rename", "section_group", {"items": [{"section_group_id": "g"}]}),
        ("reparent_page", "reparent", "page", {
            "destination_section_id": "d", "page_scope": "page_only",
            "items": [{"page_id": "p"}],
        }),
        ("reparent_section", "reparent", "section", {
            "destination_parent_id": "d", "items": [{"section_id": "s"}],
        }),
        ("reparent_section_group", "reparent", "section_group", {
            "destination_parent_id": "d", "items": [{"section_group_id": "g"}],
        }),
        ("delete_page", "delete", "page", {"items": [{"page_id": "p"}]}),
        ("delete_section", "delete", "section", {"items": [{"section_id": "s"}]}),
        ("delete_section_group", "delete", "section_group", {"items": [{"section_group_id": "g"}]}),
    ],
)
def test_every_batch_capable_original_tool_name_dispatches_items(
    monkeypatch, tool_name, family, resource_type, arguments
):
    calls = []
    monkeypatch.setattr(
        server.services.mutations,
        "batch_create",
        lambda supplied_type, parent_id, _name, _modified, items: calls.append(
            ("create", supplied_type, parent_id, items)
        ) or {"mode": "batch"},
    )
    monkeypatch.setattr(
        server.services.mutations,
        "batch_rename",
        lambda supplied_type, items: calls.append(
            ("rename", supplied_type, None, items)
        ) or {"mode": "batch"},
    )
    monkeypatch.setattr(
        server.services.mutations,
        "batch_reparent",
        lambda supplied_type, destination, items: calls.append(
            ("reparent", supplied_type, destination, items)
        ) or {"mode": "batch"},
    )
    monkeypatch.setattr(
        server.services.mutations,
        "batch_delete",
        lambda supplied_type, items: calls.append(
            ("delete", supplied_type, None, items)
        ) or {"mode": "batch"},
    )
    handler = operation_catalog.build_operation_registry(
        server.services
    ).bindings[tool_name].handler

    result = handler(arguments)

    assert result == {"mode": "batch"}
    assert calls[0][0:2] == (family, resource_type)
    assert calls[0][3] == arguments["items"]


@pytest.mark.parametrize(
    ("parent_type", "inferred"),
    [
        ("notebook", "section"),
        ("section_group", "section"),
        ("section", "page"),
        ("page", "page"),
    ],
)
def test_sort_children_infers_child_type_and_rejects_conflicts(
    monkeypatch, parent_type, inferred
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda _parent_id: {"id": "parent", "resource_type": parent_type},
    )
    calls = []
    monkeypatch.setattr(
        server.services.mutations,
        "sort_sections",
        lambda *args: calls.append(("section", args)) or {"child_type": "section"},
    )
    monkeypatch.setattr(
        server.services.mutations,
        "sort_pages",
        lambda *args: calls.append(("page", args)) or {"child_type": "page"},
    )

    result = server.services.mutations.sort_children(
        None, "parent", "Parent", None, ["child"], "name", "ascending"
    )

    assert result == {"child_type": inferred}
    assert calls[0][0] == inferred
    with pytest.raises(MutationPreflightFailure, match="conflicts"):
        server.services.mutations.sort_children(
            "page" if inferred == "section" else "section",
            "parent",
            "Parent",
            None,
            ["child"],
            "name",
            "ascending",
        )
    assert len(calls) == 1
