from __future__ import annotations

import copy
import inspect
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import operation_catalog, server
from local_onenote_mcp.services.errors import MutationPreflightFailure, PartialFailure
from local_onenote_mcp.tools.responses import caught as caught_response

pytestmark = pytest.mark.usefixtures("virtual_convergence_clock")


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


def install_page_order_backend(monkeypatch, state, events):
    plans = []

    def page_order_xml(_section, pages):
        plans.append(copy.deepcopy(pages))
        return f"<page-order-plan index='{len(plans)}' />"

    def call(operation, **params):
        if operation == "delete_hierarchy":
            object_id = str(params["object_id"])
            state["items"] = [
                value for value in state["items"] if value.get("id") != object_id
            ]
            events.append(("delete", object_id))
            return {"deleted": True}
        assert operation == "update_hierarchy"
        planned = plans.pop(0)
        section_id = str(planned[0]["section_id"])
        normalized = [
            {**value, "order": index} for index, value in enumerate(planned)
        ]
        parent_map = server.services.mutations._page_parent_map(normalized)
        normalized = [
            {**value, "parent_page_id": parent_map[str(value["id"])]}
            for value in normalized
        ]
        state["items"] = [
            value
            for value in state["items"]
            if not (
                value.get("resource_type") == "page"
                and str(value.get("section_id")) == section_id
            )
        ] + normalized
        events.append(("page_order", section_id))
        return {"updated": True}

    monkeypatch.setattr(server.services.hierarchy, "page_order_xml", page_order_xml)
    monkeypatch.setattr(server.services.mutations, "call", call)


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
            page_confirmation(value, new_title=f"New /\\:  %~界 {i}")
            for i, value in enumerate(targets)
        ]
    else:
        parent = None
        targets = [item(resource_type, f"x{i}", "n", f"Old {i}") for i in range(2)]
        supplied = [
            container_confirmation(value, new_name=f"New {i}")
            for i, value in enumerate(targets)
        ]
    state = {
        "items": [notebook, *([] if parent is None else [parent]), *targets]
    }
    install_snapshot(monkeypatch, state)
    calls = []
    if resource_type == "page":
        def rename_page(page_id, title, *_args):
            calls.append((page_id, title))
            target = next(value for value in state["items"] if value.get("id") == page_id)
            target["title"] = title
            target["name"] = title
            return {"item": {"id": page_id, "title": title}}

        monkeypatch.setattr(
            server.services.mutations,
            "update_page_title",
            rename_page,
        )
    else:
        def rename_container(object_id, supplied_type, new_name, *_args):
            calls.append((object_id, supplied_type, new_name))
            target = next(value for value in state["items"] if value.get("id") == object_id)
            target["name"] = new_name
            return {"item": {"id": object_id, "name": new_name}}

        monkeypatch.setattr(
            server.services.mutations,
            "rename_resource",
            rename_container,
        )

    result = server.services.mutations.batch_rename(resource_type, supplied)

    assert result["applied_count"] == 2
    if resource_type == "page":
        assert calls == [
            ("p0", "New /\\:  %~界 0"),
            ("p1", "New /\\:  %~界 1"),
        ]
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
                {**page_confirmation(root), "include_subpages": True},
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
    state = {
        "items": [notebook, *([] if parent is None else [parent]), *targets]
    }
    install_snapshot(monkeypatch, state)
    calls = []
    if resource_type == "page":
        def delete_page_resource(page_id, supplied_type, *_args):
            calls.append((page_id, supplied_type, _args[-1]))
            state["items"] = [
                value for value in state["items"] if value.get("id") != page_id
            ]
            return {"object_id": page_id, "permanently": False, "deleted": True}

        monkeypatch.setattr(
            server.services.mutations,
            "delete_resource",
            delete_page_resource,
        )
    else:
        def delete_container(object_id, supplied_type, *_args):
            calls.append((object_id, supplied_type, _args[-1]))
            state["items"] = [
                value for value in state["items"] if value.get("id") != object_id
            ]
            return {"object_id": object_id, "permanently": False, "deleted": True}

        monkeypatch.setattr(
            server.services.mutations,
            "delete_resource",
            delete_container,
        )

    result = server.services.mutations.batch_delete(resource_type, supplied)

    assert result["applied_count"] == 2
    assert all(entry["result"]["permanently"] is False for entry in result["items"])
    if resource_type == "page":
        assert calls == [("p0", "page", False), ("p1", "page", False)]
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
def test_batch_delete_ten_leaf_pages_ignores_more_than_two_hundred_unrelated_pages(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    targets = [
        item("page", f"target-{index}", "s", f"Target {index}", section_id="s", order=index)
        for index in range(10)
    ]
    unrelated = [
        item(
            "page",
            f"unrelated-{index}",
            "s",
            f"Unrelated {index}",
            section_id="s",
            order=10 + index,
        )
        for index in range(225)
    ]
    state = {"items": [notebook, section, *targets, *unrelated]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.CopyBudget.current",
        lambda: (_ for _ in ()).throw(
            AssertionError("batch mutation must not read CopyBudget")
        ),
    )
    calls = []

    def delete_page(page_id, _resource_type, *_args):
        calls.append(page_id)
        state["items"] = [
            value for value in state["items"] if value.get("id") != page_id
        ]
        return {"object_id": page_id, "permanently": False, "deleted": True}

    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_page)

    result = server.services.mutations.batch_delete(
        "page", [page_confirmation(value) for value in targets]
    )

    assert calls == [value["id"] for value in targets]
    assert result["applied_count"] == 10
    assert result["final_hierarchy"]["item_count"] == 10
    assert all(
        entry["status"] == "absent"
        for entry in result["final_hierarchy"]["items"]
    )


@pytest.mark.parametrize("resource_type", ["page", "section", "section_group"])
def test_batch_create_each_public_type_ignores_large_unrelated_notebook(
    monkeypatch, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    parent_section = item("section", "parent-section", "n", "Parent Section")
    unrelated_section = item("section", "unrelated-section", "n", "Unrelated")
    unrelated = [
        item(
            "page", f"unrelated-{index}", "unrelated-section", f"Unrelated {index}",
            section_id="unrelated-section", order=index,
        )
        for index in range(225)
    ]
    state = {"items": [notebook, parent_section, unrelated_section, *unrelated]}
    snapshots = []

    def resources(include_recycle_bin=False):
        snapshots.append(include_recycle_bin)
        return copy.deepcopy(state["items"])

    monkeypatch.setattr(server.services.hierarchy, "resources", resources)
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.CopyBudget.current",
        lambda: (_ for _ in ()).throw(
            AssertionError("batch Create must not read CopyBudget")
        ),
    )

    if resource_type == "page":
        parent_id = "parent-section"
        parent_name = "Parent Section"
        parent_modified = parent_section["modified"]
        supplied = [{"title": "Created"}]

        def create_page(*_args):
            created = item(
                "page", "created", parent_id, "Created",
                section_id=parent_id, order=0,
            )
            state["items"].append(created)
            return {"page_id": "created", "allocated_id": "created", "page": created}

        monkeypatch.setattr(server.services.mutations, "create_page", create_page)
    else:
        parent_id = "n"
        parent_name = "Notebook"
        parent_modified = notebook["modified"]
        supplied = [{"name": "Created"}]
        method_name = f"create_{resource_type}"

        def create_container(_parent_id, name):
            created = item(resource_type, "created", parent_id, name)
            state["items"].append(created)
            return {"allocated_id": "created", "item": created}

        monkeypatch.setattr(
            server.services.mutations, method_name, create_container
        )

    result = server.services.mutations.batch_create(
        resource_type,
        parent_id,
        parent_name,
        parent_modified,
        supplied,
    )

    assert result["applied_count"] == 1
    assert result["final_hierarchy"]["item_count"] == 1
    assert result["final_hierarchy"]["items"][0]["current_id"] == "created"
    assert snapshots == [True, True]


@pytest.mark.parametrize("family", ["rename", "reparent", "delete"])
@pytest.mark.parametrize("resource_type", ["page", "section", "section_group"])
def test_each_batch_target_type_ignores_large_unrelated_notebook(
    monkeypatch, family, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    source_section = item("section", "source", "n", "Source")
    destination_section = item("section", "destination-section", "n", "Destination")
    destination_group = item(
        "section_group", "destination-group", "n", "Destination Group"
    )
    unrelated_section = item("section", "unrelated-section", "n", "Unrelated")
    unrelated = [
        item(
            "page", f"unrelated-{index}", "unrelated-section", f"Unrelated {index}",
            section_id="unrelated-section", order=index,
        )
        for index in range(225)
    ]
    if resource_type == "page":
        target = item(
            "page", "target", "source", "Target",
            section_id="source", order=0,
        )
        supplied = page_confirmation(
            target, new_title="Renamed" if family == "rename" else None
        )
        destination_id = "destination-section"
    else:
        target = item(resource_type, "target", "n", "Target")
        supplied = container_confirmation(
            target, new_name="Renamed" if family == "rename" else None
        )
        destination_id = "destination-group"
    state = {
        "items": [
            notebook, source_section, destination_section, destination_group,
            unrelated_section, target, *unrelated,
        ]
    }
    snapshots = []

    def resources(include_recycle_bin=False):
        snapshots.append(include_recycle_bin)
        return copy.deepcopy(state["items"])

    monkeypatch.setattr(server.services.hierarchy, "resources", resources)
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.CopyBudget.current",
        lambda: (_ for _ in ()).throw(
            AssertionError("batch mutation must not read CopyBudget")
        ),
    )

    if family == "rename":
        if resource_type == "page":
            def rename_page(page_id, *_args):
                actual = next(value for value in state["items"] if value["id"] == page_id)
                actual.update(name="Renamed", title="Renamed")
                return {"item": actual}

            monkeypatch.setattr(
                server.services.mutations, "update_page_title", rename_page
            )
        else:
            def rename_container(object_id, _type, new_name, *_args):
                actual = next(value for value in state["items"] if value["id"] == object_id)
                actual["name"] = new_name
                return {"item": actual}

            monkeypatch.setattr(
                server.services.mutations, "rename_resource", rename_container
            )
        result = server.services.mutations.batch_rename(
            resource_type, [supplied]
        )
    elif family == "reparent":
        if resource_type == "page":
            def reparent_page(page_id, destination_id, *_args):
                actual = next(value for value in state["items"] if value["id"] == page_id)
                actual.update(
                    parent_id=destination_id,
                    section_id=destination_id,
                    page_level=1,
                    parent_page_id=None,
                )
                return {"item": actual, "id_map": {page_id: page_id}}

            monkeypatch.setattr(
                server.services.mutations, "reparent_page", reparent_page
            )
        else:
            def reparent_container(object_id, destination_id, *_args):
                actual = next(value for value in state["items"] if value["id"] == object_id)
                actual["parent_id"] = destination_id
                return {"item": actual}

            monkeypatch.setattr(
                server.services.mutations,
                f"reparent_{resource_type}",
                reparent_container,
            )
        result = server.services.mutations.batch_reparent(
            resource_type, destination_id, [supplied]
        )
    else:
        def delete_resource(object_id, *_args):
            state["items"] = [
                value for value in state["items"] if value.get("id") != object_id
            ]
            return {"object_id": object_id, "permanently": False}

        monkeypatch.setattr(
            server.services.mutations, "delete_resource", delete_resource
        )
        result = server.services.mutations.batch_delete(
            resource_type, [supplied]
        )

    assert result["applied_count"] == 1
    assert result["final_hierarchy"]["item_count"] == 1
    assert snapshots == [True, True]


@pytest.mark.parametrize("include_subpages", [False, True])
def test_batch_delete_effective_page_scope_budget_fails_before_mutation(
    monkeypatch, include_subpages
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES", "2")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    descendants = [
        item(
            "page", "child", "s", "Child", section_id="s", order=1,
            level=2, parent_page_id="root",
        ),
        item(
            "page", "grandchild", "s", "Grandchild", section_id="s", order=2,
            level=3, parent_page_id="child",
        ),
    ]
    install_snapshot(
        monkeypatch, {"items": [notebook, section, root, *descendants]}
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not delete")),
    )

    with pytest.raises(MutationPreflightFailure) as caught:
        server.services.mutations.batch_delete(
            "page",
            [{**page_confirmation(root), "include_subpages": include_subpages}],
        )

    assert caught.value.details == {
        "mutation_stage": "preflight",
        "mutation_attempted": False,
        "budget_dimension": "effective_pages",
        "observed_count": 3,
        "configured_limit": 2,
        "content_exposed": False,
    }


def test_batch_page_scope_union_budget_fails_before_any_promotion_or_delete(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES", "3")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    first = item("page", "first", "s", "First", section_id="s", order=0)
    first_child = item(
        "page", "first-child", "s", "First Child", section_id="s", order=1,
        level=2, parent_page_id="first",
    )
    second = item("page", "second", "s", "Second", section_id="s", order=2)
    second_child = item(
        "page", "second-child", "s", "Second Child", section_id="s", order=3,
        level=2, parent_page_id="second",
    )
    install_snapshot(
        monkeypatch,
        {"items": [notebook, section, first, first_child, second, second_child]},
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("budget rejection must precede descendant promotion")
        ),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("budget rejection must precede principal Delete")
        ),
    )

    with pytest.raises(MutationPreflightFailure) as caught:
        server.services.mutations.batch_delete(
            "page",
            [
                {**page_confirmation(first), "include_subpages": False},
                {**page_confirmation(second), "include_subpages": True},
            ],
        )

    assert caught.value.details["budget_dimension"] == "effective_pages"
    assert caught.value.details["observed_count"] == 4
    assert caught.value.details["configured_limit"] == 3
    assert caught.value.details["mutation_attempted"] is False


@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_batch_container_scope_budget_counts_complete_descendants_before_delete(
    monkeypatch, resource_type
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    if resource_type == "section":
        root = item("section", "root", "n", "Root")
        descendants = [
            item("page", "page", "root", "Page", section_id="root", order=0)
        ]
        configured_limit = 1
    else:
        root = item("section_group", "root", "n", "Root")
        child_section = item("section", "child-section", "root", "Child")
        descendants = [
            child_section,
            item(
                "page", "page", "child-section", "Page",
                section_id="child-section", order=0,
            ),
        ]
        configured_limit = 2
    install_snapshot(monkeypatch, {"items": [notebook, root, *descendants]})
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.BatchMutationBudget.current",
        lambda: type(
            "Budget",
            (),
            {
                "max_catalog_resources": 100,
                "max_effective_resources": configured_limit,
                "max_effective_pages": 100,
                "max_direct_siblings": 100,
                "max_page_content_chars": 100_000,
            },
        )(),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must reject the complete container scope")
        ),
    )

    with pytest.raises(MutationPreflightFailure) as caught:
        server.services.mutations.batch_delete(
            resource_type, [container_confirmation(root)]
        )

    assert caught.value.details["budget_dimension"] == "effective_resources"
    assert caught.value.details["observed_count"] == len(descendants) + 1
    assert caught.value.details["configured_limit"] == configured_limit
    assert caught.value.details["mutation_attempted"] is False


@pytest.mark.parametrize("include_subpages", [False, True])
def test_delete_page_protects_or_deletes_complete_subpage_scope(
    monkeypatch, include_subpages
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    grandchild = item(
        "page", "grandchild", "s", "Grandchild", section_id="s", order=2,
        level=3, parent_page_id="child"
    )
    sibling = item("page", "sibling", "s", "Sibling", section_id="s", order=3)
    state = {"items": [notebook, section, root, child, grandchild, sibling]}
    install_snapshot(monkeypatch, state)
    events = []
    install_page_order_backend(monkeypatch, state, events)

    result = server.services.mutations.delete_page(
        "root", "Root", "s", root["modified"], False, include_subpages
    )

    active_pages = sorted(
        (
            value
            for value in state["items"]
            if value.get("resource_type") == "page"
        ),
        key=lambda value: int(value.get("order", 0)),
    )
    assert result["include_subpages"] is include_subpages
    if include_subpages:
        assert events == [
            ("delete", "grandchild"),
            ("delete", "child"),
            ("delete", "root"),
        ]
        assert [value["id"] for value in active_pages] == ["sibling"]
    else:
        assert events == [("page_order", "s"), ("delete", "root")]
        assert [value["id"] for value in active_pages] == [
            "child", "grandchild", "sibling"
        ]
        assert [value["page_level"] for value in active_pages] == [1, 2, 1]
        assert [value["parent_page_id"] for value in active_pages] == [
            None, "child", None
        ]
        assert result["final_hierarchy"]["protected_active"] is True


@pytest.mark.parametrize("include_subpages", [False, True])
def test_reorder_page_protects_subpages_or_moves_complete_block(
    monkeypatch, include_subpages
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    grandchild = item(
        "page", "grandchild", "s", "Grandchild", section_id="s", order=2,
        level=3, parent_page_id="child"
    )
    sibling = item("page", "sibling", "s", "Sibling", section_id="s", order=3)
    state = {"items": [notebook, section, root, child, grandchild, sibling]}
    install_snapshot(monkeypatch, state)
    events = []
    install_page_order_backend(monkeypatch, state, events)
    monkeypatch.setattr(server.services.pages, "confirm", lambda *_args, **_kwargs: root)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, *_args: next(
            value for value in state["items"] if value.get("id") == object_id
        ),
    )

    result = server.services.mutations.reorder_page(
        "root", "Root", "s", "sibling", 1, root["modified"], include_subpages
    )

    pages = sorted(
        (value for value in state["items"] if value.get("resource_type") == "page"),
        key=lambda value: int(value["order"]),
    )
    if include_subpages:
        assert events == [("page_order", "s")]
        assert [value["id"] for value in pages] == [
            "sibling", "root", "child", "grandchild"
        ]
        assert [value["page_level"] for value in pages] == [1, 1, 2, 3]
    else:
        assert events == [("page_order", "s"), ("page_order", "s")]
        assert [value["id"] for value in pages] == [
            "child", "grandchild", "sibling", "root"
        ]
        assert [value["page_level"] for value in pages] == [1, 2, 1, 1]
    assert result["include_subpages"] is include_subpages
    assert result["verification_scope"] == {"page_content": "not_read"}


def test_batch_delete_plans_all_page_protection_before_principal_items(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    leaf = item("page", "leaf", "s", "Leaf", section_id="s", order=2)
    state = {"items": [notebook, section, root, child, leaf]}
    install_snapshot(monkeypatch, state)
    events = []
    install_page_order_backend(monkeypatch, state, events)

    def delete_resource(object_id, _resource_type, *_args):
        events.append(("delete", object_id))
        state["items"] = [
            value for value in state["items"] if value.get("id") != object_id
        ]
        return {"object_id": object_id, "permanently": False, "deleted": True}

    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_resource)
    result = server.services.mutations.batch_delete(
        "page",
        [
            {**page_confirmation(root), "include_subpages": False},
            {**page_confirmation(leaf), "include_subpages": True},
        ],
    )

    assert events == [
        ("page_order", "s"),
        ("delete", "root"),
        ("delete", "leaf"),
    ]
    assert result["preserved_descendants"]["preserved_descendant_ids"] == [
        "child"
    ]
    assert result["final_hierarchy"]["protected_count"] == 1
    current_child = next(
        value for value in state["items"] if value.get("id") == "child"
    )
    assert (current_child["page_level"], current_child["parent_page_id"]) == (
        1, None
    )


def test_batch_page_protection_failure_starts_no_principal_delete(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    state = {"items": [notebook, section, root, child]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(
        server.services.hierarchy,
        "page_order_xml",
        lambda *_args, **_kwargs: "<planned />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("uncertain")),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("principal delete must not start")
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.batch_delete(
            "page", [{**page_confirmation(root), "include_subpages": False}]
        )

    assert caught.value.details["principal_mutation_attempted"] is False
    assert caught.value.details["completed_section_count"] == 0
    assert caught.value.details["rollback_attempted"] is False
    assert caught.value.details["mutation_replayed"] is False


@pytest.mark.parametrize("operation", ["delete", "reorder"])
def test_single_page_protection_failure_starts_no_principal_operation(
    monkeypatch, operation
):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    sibling = item("page", "sibling", "s", "Sibling", section_id="s", order=2)
    state = {"items": [notebook, section, root, child, sibling]}
    install_snapshot(monkeypatch, state)
    monkeypatch.setattr(server.services.pages, "confirm", lambda *_a, **_k: root)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda *_args, **_kwargs: section,
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "page_order_xml",
        lambda *_args, **_kwargs: "<planned />",
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("uncertain")),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "delete_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("principal delete must not start")
        ),
    )

    with pytest.raises(PartialFailure) as caught:
        if operation == "delete":
            server.services.mutations.delete_page(
                "root", "Root", "s", root["modified"], False, False
            )
        else:
            server.services.mutations.reorder_page(
                "root", "Root", "s", "sibling", 1, root["modified"], False
            )

    assert caught.value.details["principal_mutation_attempted"] is False
    assert caught.value.details["rollback_attempted"] is False
    assert caught.value.details["mutation_replayed"] is False


def test_batch_subtree_delete_reports_inner_scope_progress(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page", "child", "s", "Child", section_id="s", order=1,
        level=2, parent_page_id="root"
    )
    grandchild = item(
        "page", "grandchild", "s", "Grandchild", section_id="s", order=2,
        level=3, parent_page_id="child"
    )
    install_snapshot(
        monkeypatch, {"items": [notebook, section, root, child, grandchild]}
    )
    calls = []

    def fail_on_child(object_id, *_args):
        calls.append(object_id)
        if object_id == "child":
            raise RuntimeError("uncertain")
        return {"object_id": object_id, "permanently": False}

    monkeypatch.setattr(server.services.mutations, "delete_resource", fail_on_child)

    with pytest.raises(PartialFailure) as caught:
        server.services.mutations.batch_delete(
            "page", [{**page_confirmation(root), "include_subpages": True}]
        )

    assert calls == ["grandchild", "child"]
    nested = caught.value.details["failed_item_details"]
    assert nested["operation"] == "delete_page_scope"
    assert nested["include_subpages"] is True
    assert [entry["object_id"] for entry in nested["items"]] == [
        "grandchild", "child", "root"
    ]
    assert [entry["status"] for entry in nested["items"]] == [
        "applied", "failed", "not_attempted"
    ]
    assert nested["rollback_attempted"] is False
    assert nested["mutation_replayed"] is False


def test_batch_reparent_uses_frozen_mixed_scope_and_batch_wide_protection(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", "true")
    notebook = item("notebook", "n", None, "Notebook")
    source = item("section", "source", "n", "Source")
    destination = item("section", "destination", "n", "Destination")
    protected_root = item(
        "page", "protected-root", "source", "Protected Root",
        section_id="source", order=0
    )
    protected_child = item(
        "page", "protected-child", "source", "Protected Child",
        section_id="source", order=1, level=2, parent_page_id="protected-root"
    )
    subtree_root = item(
        "page", "subtree-root", "source", "Subtree Root",
        section_id="source", order=2
    )
    subtree_child = item(
        "page", "subtree-child", "source", "Subtree Child",
        section_id="source", order=3, level=2, parent_page_id="subtree-root"
    )
    state = {
        "items": [
            notebook, source, destination, protected_root, protected_child,
            subtree_root, subtree_child,
        ]
    }
    install_snapshot(monkeypatch, state)
    events = []
    install_page_order_backend(monkeypatch, state, events)

    def reparent_page(page_id, destination_id, *_args):
        events.append(("reparent", page_id))
        pages = sorted(
            (
                value for value in state["items"]
                if value.get("resource_type") == "page"
                and value.get("section_id") == "source"
            ),
            key=lambda value: int(value["order"]),
        )
        scope = server.services.mutations._page_scope(pages, page_id)
        moved_ids = {str(value["id"]) for value in scope}
        root_level = int(scope[0]["page_level"])
        moved = [
            {
                **value,
                "section_id": destination_id,
                "parent_id": destination_id,
                "order": index,
                "page_level": int(value["page_level"]) - root_level + 1,
            }
            for index, value in enumerate(scope)
        ]
        moved_parent_map = server.services.mutations._page_parent_map(moved)
        moved = [
            {**value, "parent_page_id": moved_parent_map[str(value["id"])]}
            for value in moved
        ]
        state["items"] = [
            value for value in state["items"] if str(value.get("id")) not in moved_ids
        ] + moved
        return {
            "item": moved[0],
            "id_map": {str(value["id"]): str(value["id"]) for value in scope},
        }

    monkeypatch.setattr(server.services.mutations, "reparent_page", reparent_page)
    result = server.services.mutations.batch_reparent(
        "page",
        "destination",
        [
            {**page_confirmation(protected_root), "include_subpages": False},
            {**page_confirmation(subtree_root), "include_subpages": True},
        ],
    )

    assert events == [
        ("page_order", "source"),
        ("reparent", "protected-root"),
        ("reparent", "subtree-root"),
    ]
    assert result["final_hierarchy"]["scope_item_count"] == 3
    assert result["final_hierarchy"]["protected_count"] == 1
    assert result["preserved_descendants"]["preserved_descendant_ids"] == [
        "protected-child"
    ]
    current_protected = next(
        value for value in state["items"] if value.get("id") == "protected-child"
    )
    assert current_protected["section_id"] == "source"
    assert (current_protected["page_level"], current_protected["parent_page_id"]) == (
        1, None
    )


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
        state["items"].append(
            item(
                "page",
                allocated_id,
                section_id,
                title,
                section_id=section_id,
                order=len(calls) - 1,
            )
        )
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
        [
            {"title": "Same /\\:  %~界"},
            {"title": "Same /\\:  %~界"},
        ],
    )

    assert result["applied_count"] == 2
    assert calls == [
        ("s", "Same /\\:  %~界"),
        ("s", "Same /\\:  %~界"),
    ]
    assert [entry["result"]["allocated_id"] for entry in result["items"]] == [
        "p1", "p2"
    ]


@pytest.mark.parametrize("resource_type", ["section", "section_group"])
def test_batch_create_container_returns_each_allocated_identity(monkeypatch, resource_type):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    parent = item("notebook", "n", None, "Notebook")
    state = {"items": [parent]}
    install_snapshot(monkeypatch, state)
    calls = []
    method_name = "create_section" if resource_type == "section" else "create_section_group"

    def create(parent_id, name):
        calls.append((parent_id, name))
        state["items"].append(
            item(resource_type, f"allocated-{len(calls)}", parent_id, name)
        )
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
        "local_onenote_mcp.services.mutations.BatchMutationBudget.current",
        lambda: type(
            "Budget",
            (),
            {
                "max_catalog_resources": 100,
                "max_effective_resources": 2,
                "max_effective_pages": 100,
                "max_direct_siblings": 100,
                "max_page_content_chars": 500_000,
            },
        )(),
    )
    with pytest.raises(MutationPreflightFailure, match="effective resource budget"):
        server.services.mutations.batch_create(
            "section",
            "n",
            "Notebook",
            notebook["modified"],
            [{"name": "One"}, {"name": "Two"}],
        )

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.BatchMutationBudget.current",
        lambda: type(
            "Budget",
            (),
            {
                "max_catalog_resources": 100,
                "max_effective_resources": 100,
                "max_effective_pages": 100,
                "max_direct_siblings": 100,
                "max_page_content_chars": 500_000,
            },
        )(),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "create_page",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    with pytest.raises(MutationPreflightFailure, match="content character budget"):
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


@pytest.mark.parametrize("family", ["create", "rename", "delete"])
def test_successful_item_calls_require_complete_batch_final_hierarchy_readback(
    monkeypatch, family
):
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    target = item("page", "p", "s", "Old", section_id="s", order=0)
    state = {"items": [notebook, section, target]}
    install_snapshot(monkeypatch, state)

    if family == "create":
        monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
        monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
        monkeypatch.setattr(
            server.services.mutations,
            "create_page",
            lambda *_args: {"page_id": "missing", "allocated_id": "missing"},
        )
        execute = lambda: server.services.mutations.batch_create(
            "page", "s", "Section", section["modified"], [{"title": "New"}]
        )
        operation = "create_page"
    elif family == "rename":
        monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
        monkeypatch.setattr(
            server.services.mutations,
            "update_page_title",
            lambda *_args: {"item": {"id": "p", "title": "New"}},
        )
        execute = lambda: server.services.mutations.batch_rename(
            "page", [page_confirmation(target, new_title="New")]
        )
        operation = "rename_page"
    else:
        monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
        monkeypatch.setattr(
            server.services.mutations,
            "delete_resource",
            lambda *_args: {
                "object_id": "p",
                "permanently": False,
                "deleted": True,
            },
        )
        execute = lambda: server.services.mutations.batch_delete(
            "page", [page_confirmation(target)]
        )
        operation = "delete_page"

    with pytest.raises(PartialFailure) as caught:
        execute()

    assert caught.value.details["operation"] == operation
    assert caught.value.details["applied_count"] == 1
    assert caught.value.details["failed_step"] == "batch_final_hierarchy"
    assert caught.value.details["rollback_attempted"] is False
    assert caught.value.details["mutation_replayed"] is False


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
    discovered_batch_tools = {
        name
        for name, tool in tools.items()
        if "items" in tool.parameters.get("properties", {})
    }
    assert discovered_batch_tools == batch_tools
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


def test_every_batch_service_path_is_decoupled_from_copy_budget():
    for method_name in (
        "_batch_snapshot",
        "_preflight_batch_targets",
        "_capture_reparent_hierarchy",
        "batch_create",
        "batch_rename",
        "batch_reparent",
        "batch_delete",
    ):
        source = inspect.getsource(getattr(server.services.mutations, method_name))
        assert "CopyBudget" not in source
        assert "LOCAL_ONENOTE_MAX_COPY_" not in source


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
            "include_subpages": True,
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
            "destination_section_id": "d", "include_subpages": False,
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


def _page_order_catalog() -> list[dict]:
    notebook = item("notebook", "n", None, "Notebook")
    section = item("section", "s", "n", "Section")
    root = item("page", "root", "s", "Root", section_id="s", order=0)
    child = item(
        "page",
        "child",
        "s",
        "Child",
        section_id="s",
        order=1,
        level=2,
        parent_page_id="root",
    )
    return [notebook, section, root, child]


def test_page_order_xml_catalog_accepts_nested_pages_and_uses_canonical_names():
    catalog = _page_order_catalog()
    section = catalog[1]
    pages = [
        {**catalog[2], "page_level": 1, "title": "Ignored Root Title"},
        {**catalog[3], "page_level": 1, "title": "Ignored Child Title"},
    ]
    xml = server.services.hierarchy.page_order_xml(section, pages, catalog=catalog)
    assert 'ID="root"' in xml
    assert 'ID="child"' in xml
    assert 'name="Root"' in xml
    assert 'name="Child"' in xml
    assert 'pageLevel="1"' in xml


@pytest.mark.parametrize(
    "broken",
    ["missing_ancestor", "wrong_section", "duplicate_id", "missing_sibling", "extra_page"],
)
def test_page_order_xml_catalog_fail_closed(broken):
    catalog = _page_order_catalog()
    section = catalog[1]
    pages = [
        {**catalog[2], "page_level": 1},
        {**catalog[3], "page_level": 2},
    ]
    if broken == "missing_ancestor":
        catalog = [item for item in catalog if item["id"] != "n"]
    elif broken == "wrong_section":
        pages[1] = {**pages[1], "section_id": "other"}
    elif broken == "duplicate_id":
        pages.append({**pages[0], "page_level": 1})
    elif broken == "missing_sibling":
        pages = pages[:1]
    else:
        pages.append(item("page", "extra", "s", "Extra", section_id="s", order=2))
    with pytest.raises((ValueError, RuntimeError)):
        server.services.hierarchy.page_order_xml(section, pages, catalog=catalog)
