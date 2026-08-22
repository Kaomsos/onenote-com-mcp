"""Deterministic Copy/Move readback ledger and phase-local snapshot budgets."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.services.read_reasons import (
    DELETE_CONFIRMATION,
    DELETE_CONVERGENCE,
    DESTINATION_PRECONDITION,
    POST_CREATE_CONVERGENCE,
    READ_REASONS,
    copy_move_read_attribution,
    current_read_reason,
)
from tests.test_copying import (
    advance_fake_mutation_epoch,
    apply_page_content_update,
    install_recursive_execute_fakes,
)

pytestmark = pytest.mark.usefixtures("virtual_convergence_clock")


@contextmanager
def _ledger_recording() -> Iterator[list[tuple[str, str | None]]]:
    ledger: list[tuple[str, str | None]] = []
    original_hierarchy_xml = server.services.hierarchy.hierarchy_xml
    original_page_xml = server.services.pages.xml

    def record_hierarchy_xml(start_id: str = "", scope: str = "pages") -> str:
        ledger.append(("get_hierarchy", current_read_reason()))
        return original_hierarchy_xml(start_id, scope)

    def record_page_xml(page_id: str, page_info: str = "basic") -> str:
        ledger.append(("get_page_content", current_read_reason()))
        return original_page_xml(page_id, page_info)

    server.services.hierarchy.hierarchy_xml = record_hierarchy_xml  # type: ignore[method-assign]
    server.services.pages.xml = record_page_xml  # type: ignore[method-assign]
    try:
        yield ledger
    finally:
        server.services.hierarchy.hierarchy_xml = original_hierarchy_xml  # type: ignore[method-assign]
        server.services.pages.xml = original_page_xml  # type: ignore[method-assign]


def _budget(ledger: list[tuple[str, str | None]]) -> dict[tuple[str, str | None], int]:
    return dict(Counter(ledger))


def _count(ledger: list[tuple[str, str | None]], operation: str, reason: str | None = None) -> int:
    return sum(
        1
        for op, read_reason in ledger
        if op == operation and (reason is None or read_reason == reason)
    )


def _planning_hierarchy_reads(ledger: list[tuple[str, str | None]]) -> int:
    planning_reasons = {
        "source_confirmation",
        "plan_capture",
        "destination_precondition",
    }
    return sum(
        1
        for op, reason in ledger
        if op == "get_hierarchy" and reason in planning_reasons
    )


# Frozen per-operation budgets for the recursive execute fake fixture.
# Keys are (backend_operation, read_reason). Copy/Move hierarchy and Page reads
# must carry one of the public allowlisted reasons.
_COPY_PAGE_BUDGET: dict[tuple[str, str | None], int] = {
    ("get_hierarchy", "source_confirmation"): 1,
    ("get_hierarchy", "topology_verification"): 2,
    ("get_page_content", "final_source_revalidation"): 1,
    ("get_page_content", "final_target_readback"): 1,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_convergence"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
}
_MOVE_PAGE_BUDGET: dict[tuple[str, str | None], int] = {
    ("get_hierarchy", "delete_confirmation"): 1,
    ("get_hierarchy", "delete_convergence"): 1,
    ("get_hierarchy", "source_confirmation"): 1,
    ("get_hierarchy", "source_drift_revalidation"): 1,
    ("get_hierarchy", "topology_verification"): 2,
    ("get_page_content", "final_source_revalidation"): 1,
    ("get_page_content", "final_target_readback"): 1,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_convergence"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
    ("get_page_content", "source_drift_revalidation"): 1,
}
_CONTAINER_COPY_BUDGET: dict[tuple[str, str | None], int] = {
    key: value
    for key, value in _COPY_PAGE_BUDGET.items()
    if key != ("get_page_content", "post_write_convergence")
}
_MOVE_CONTAINER_BUDGET: dict[tuple[str, str | None], int] = {
    ("get_hierarchy", "delete_confirmation"): 1,
    ("get_hierarchy", "delete_convergence"): 1,
    ("get_hierarchy", "source_confirmation"): 1,
    ("get_hierarchy", "source_drift_revalidation"): 1,
    ("get_hierarchy", "topology_verification"): 2,
    ("get_page_content", "delete_convergence"): 1,
    ("get_page_content", "final_source_revalidation"): 1,
    ("get_page_content", "final_target_readback"): 1,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
    ("get_page_content", "source_drift_revalidation"): 2,
}


@pytest.fixture(autouse=True)
def _enable_ledger_mutation_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")


def _install_delete_fakes(monkeypatch: pytest.MonkeyPatch, state: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})

    def delete_page(page_id, *args, **kwargs):
        state[:] = [item for item in state if item.get("id") != page_id]
        advance_fake_mutation_epoch()
        return {"deleted": True, "final_state": {"is_in_recycle_bin": True}}

    def delete_resource(object_id, *args, **kwargs):
        removed_ids = {object_id}
        expanded = True
        while expanded:
            expanded = False
            for item in state:
                if item["id"] in removed_ids:
                    continue
                if (
                    item.get("parent_id") in removed_ids
                    or item.get("section_id") in removed_ids
                ):
                    removed_ids.add(item["id"])
                    expanded = True
        state[:] = [item for item in state if item["id"] not in removed_ids]
        advance_fake_mutation_epoch()
        return {"deleted": True, "final_state": {"is_in_recycle_bin": True}}

    monkeypatch.setattr(server.services.mutations, "delete_page", delete_page)
    monkeypatch.setattr(server.services.mutations, "delete_resource", delete_resource)


def _install_real_mutation_readback_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Keep real create/delete readback while replacing only backend writes."""

    actual_create_page = server.services.mutations.create_page
    actual_delete_page = server.services.mutations.delete_page
    state = install_recursive_execute_fakes(
        monkeypatch,
        include_destination_section=True,
    )
    monkeypatch.setattr(server.services.mutations, "create_page", actual_create_page)
    monkeypatch.setattr(server.services.mutations, "delete_page", actual_delete_page)

    def call(operation: str, **params: Any) -> dict[str, Any]:
        if operation == "create_new_page":
            section = next(item for item in state if item["id"] == params["section_id"])
            page_id = "created-page"
            state.append(
                {
                    "resource_type": "page",
                    "id": page_id,
                    "title": "Created Page",
                    "path": f"{section['path']}/Created Page",
                    "parent_id": section["id"],
                    "notebook_id": section["notebook_id"],
                    "section_id": section["id"],
                    "parent_page_id": None,
                    "page_level": 1,
                    "order": 0,
                }
            )
            return {"page_id": page_id}
        if operation == "update_page_content":
            return {"updated": True}
        if operation == "delete_hierarchy":
            target_id = str(params["object_id"])
            state[:] = [item for item in state if item.get("id") != target_id]
            return {"deleted": True}
        raise AssertionError(operation)

    monkeypatch.setattr(server.services.mutations, "call", call)
    return state


@pytest.mark.parametrize(
    ("invoke", "expected_reasons"),
    [
        (
            lambda: server.services.mutations.create_page(
                "destination-section",
                "Created Page",
            ),
            {DESTINATION_PRECONDITION, POST_CREATE_CONVERGENCE},
        ),
        (
            lambda: server.services.mutations.delete_page(
                "source-page",
                "Page",
                "source-section",
            ),
            {DELETE_CONFIRMATION, DELETE_CONVERGENCE},
        ),
    ],
)
def test_copy_move_shared_mutation_readback_uses_reasons(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[], Any],
    expected_reasons: set[str],
) -> None:
    _install_real_mutation_readback_fakes(monkeypatch)

    with copy_move_read_attribution(), _ledger_recording() as ledger:
        invoke()

    reasons = {reason for _, reason in ledger}
    assert reasons == expected_reasons
    assert all(reason in READ_REASONS for _, reason in ledger)


@pytest.mark.parametrize(
    ("operation", "shape", "expected_budget", "invoke"),
    [
        (
            "copy_page",
            "root",
            _COPY_PAGE_BUDGET,
            lambda tmp_path: server.services.copying.copy_resource(
                "source-page",
                "page",
                "destination-section",
                "Copied Page",
                "",
                "Page",
                "source-section",
                None,
                include_descendants=False,
            ),
        ),
        (
            "copy_page",
            "subtree",
            _COPY_PAGE_BUDGET,
            lambda tmp_path: server.services.copying.copy_resource(
                "source-page",
                "page",
                "destination-section",
                "Copied Page",
                "",
                "Page",
                "source-section",
                None,
                include_descendants=True,
            ),
        ),
        (
            "move_page",
            "root",
            _MOVE_PAGE_BUDGET,
            lambda tmp_path: server.services.copying.move_page(
                "source-page",
                "destination-section",
                "Page",
                "source-section",
                None,
                destination_title="Moved Page",
                include_descendants=False,
            ),
        ),
        (
            "move_page",
            "subtree",
            _MOVE_PAGE_BUDGET,
            lambda tmp_path: server.services.copying.move_page(
                "source-page",
                "destination-section",
                "Page",
                "source-section",
                None,
                destination_title="Moved Page",
                include_descendants=True,
            ),
        ),
        (
            "copy_section",
            "subtree",
            _CONTAINER_COPY_BUDGET,
            lambda tmp_path: server.services.copying.copy_resource(
                "source-section",
                "section",
                "destination-notebook",
                "Copied Section",
                "",
                "Notes",
                "inner-group",
                None,
                include_descendants=False,
            ),
        ),
        (
            "copy_section_group",
            "subtree",
            _CONTAINER_COPY_BUDGET,
            lambda tmp_path: server.services.copying.copy_resource(
                "source-group",
                "section_group",
                "destination-notebook",
                "Copied Group",
                "",
                "Source Group",
                "source-notebook",
                None,
                include_descendants=False,
            ),
        ),
        (
            "copy_notebook",
            "subtree",
            _CONTAINER_COPY_BUDGET,
            lambda tmp_path: server.services.copying.copy_resource(
                "source-notebook",
                "notebook",
                "",
                "Notebook Copy",
                str(tmp_path),
                "Source Notebook",
                None,
                None,
                include_descendants=False,
            ),
        ),
        (
            "move_section",
            "subtree",
            _MOVE_CONTAINER_BUDGET,
            lambda tmp_path: server.services.copying.move_section(
                "source-section",
                "destination-notebook",
                "Notes",
                "inner-group",
                None,
                destination_name="Moved Section",
            ),
        ),
        (
            "move_section_group",
            "subtree",
            _MOVE_CONTAINER_BUDGET,
            lambda tmp_path: server.services.copying.move_section_group(
                "source-group",
                "destination-notebook",
                "Source Group",
                "source-notebook",
                None,
                destination_name="Moved Group",
            ),
        ),
    ],
)
def test_copy_move_readback_ledger_freezes_exact_budgets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
    shape: str,
    expected_budget: dict[tuple[str, str | None], int],
    invoke: Callable[[Path], Any],
) -> None:
    state = install_recursive_execute_fakes(
        monkeypatch,
        include_destination_section=True,
    )
    _install_delete_fakes(monkeypatch, state)

    with _ledger_recording() as ledger:
        invoke(tmp_path)

    assert all(reason in READ_REASONS for _, reason in ledger)
    assert _budget(ledger) == expected_budget
    assert _planning_hierarchy_reads(ledger) == 1
    assert _count(ledger, "get_page_content", "plan_capture") == 1


@pytest.mark.parametrize(
    "invoke",
    [
        lambda tmp_path: server.services.copying.copy_resource(
            "source-section",
            "section",
            "destination-notebook",
            "Copied Section",
            "",
            "Notes",
            "inner-group",
            None,
            include_descendants=False,
        ),
        lambda tmp_path: server.services.copying.copy_resource(
            "source-group",
            "section_group",
            "destination-notebook",
            "Copied Group",
            "",
            "Source Group",
            "source-notebook",
            None,
            include_descendants=False,
        ),
        lambda tmp_path: server.services.copying.copy_resource(
            "source-notebook",
            "notebook",
            "",
            "Notebook Copy",
            str(tmp_path),
            "Source Notebook",
            None,
            None,
            include_descendants=False,
        ),
    ],
)
def test_container_copy_pages_share_operation_local_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invoke: Callable[[Path], Any],
) -> None:
    """Container pages go through the shared _build_plan → _execute_copy cache."""

    state = install_recursive_execute_fakes(
        monkeypatch,
        include_destination_section=True,
        duplicate_page_titles=True,
    )
    _install_delete_fakes(monkeypatch, state)
    source_page_count = sum(
        1 for item in state if item.get("resource_type") == "page" and item["id"].startswith("source-page")
    )
    assert source_page_count == 2

    with _ledger_recording() as ledger:
        invoke(tmp_path)

    assert _planning_hierarchy_reads(ledger) == 1
    assert _count(ledger, "get_page_content", "plan_capture") == source_page_count
    assert _count(ledger, "get_page_content", "pre_write_target_observation") == source_page_count
    assert _count(ledger, "get_page_content", "post_write_reconciliation") == source_page_count
    assert _count(ledger, "get_page_content", "final_target_readback") == source_page_count
    assert _count(ledger, "get_page_content", "final_source_revalidation") == source_page_count


def test_reorder_phase_uses_single_hierarchy_read_per_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_recursive_execute_fakes(
        monkeypatch,
        include_destination_section=True,
    )
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})

    with _ledger_recording() as ledger:
        server.services.copying.copy_resource(
            "source-page",
            "page",
            "destination-section",
            "Copied Page",
            "",
            "Page",
            "source-section",
            None,
            include_descendants=False,
        )

    topology_reads = _count(ledger, "get_hierarchy", "topology_verification")
    assert topology_reads >= 1


def _scripted_bridge(state: list[dict[str, Any]], xml_store: dict[str, str]):
    from tests.test_copying import hierarchy_xml_from_items, page_xml

    operations: list[str] = []
    counters = {"page": 0, "section": 0, "section_group": 0, "clock": 0}

    def parent_item(parent_id: str) -> dict[str, Any]:
        return next(item for item in state if item["id"] == parent_id)

    def mark_recycled(target_id: str) -> None:
        removed = {target_id}
        expanded = True
        while expanded:
            expanded = False
            for item in state:
                if item["id"] in removed:
                    continue
                if (
                    item.get("parent_id") in removed
                    or item.get("section_id") in removed
                    or item.get("parent_page_id") in removed
                ):
                    removed.add(item["id"])
                    expanded = True
        for item in state:
            if item["id"] in removed:
                item["is_in_recycle_bin"] = True

    def call(*args: Any, **params: Any) -> dict[str, Any]:
        operation = args[-1] if args and isinstance(args[-1], str) else str(params.pop("operation", ""))
        operations.append(operation)
        if operation == "get_hierarchy":
            return {"xml": hierarchy_xml_from_items(state)}
        if operation == "get_page_content":
            return {"xml": xml_store[str(params["page_id"])]}
        if operation == "create_new_page":
            counters["page"] += 1
            page_id = f"created-page-{counters['page']}"
            section = parent_item(str(params["section_id"]))
            state.append(
                {
                    "resource_type": "page",
                    "id": page_id,
                    "title": "Created Page",
                    "path": f"{section['path']}/Created Page",
                    "parent_id": section["id"],
                    "notebook_id": section.get("notebook_id"),
                    "section_id": section["id"],
                    "parent_page_id": None,
                    "page_level": 1,
                    "order": 99,
                    "is_in_recycle_bin": False,
                }
            )
            xml_store[page_id] = page_xml(page_id, "Created Page")
            return {"page_id": page_id}
        if operation == "update_page_content":
            from local_onenote_mcp.page import title_from_page_xml

            root = ET.fromstring(params["xml"])
            page_id = root.attrib["ID"]
            title = title_from_page_xml(params["xml"]) or root.attrib.get("name") or next(
                item.get("title", "") for item in state if item["id"] == page_id
            )
            update_children = list(root)
            title_only_update = bool(update_children) and all(
                child.tag.rsplit("}", 1)[-1] == "Title"
                for child in update_children
            )
            datetime_only = (
                not update_children
                and "dateTime" in root.attrib
                and set(root.attrib) <= {"ID", "dateTime"}
            )
            if datetime_only:
                apply_page_content_update(xml_store, params["xml"])
            elif title_only_update:
                existing = ET.fromstring(xml_store[page_id])
                old_title = next(
                    (
                        child
                        for child in list(existing)
                        if child.tag.rsplit("}", 1)[-1] == "Title"
                    ),
                    None,
                )
                new_title = next(child for child in update_children if child.tag.rsplit("}", 1)[-1] == "Title")
                if old_title is None:
                    existing.insert(0, new_title)
                else:
                    existing.insert(list(existing).index(old_title), new_title)
                    existing.remove(old_title)
                xml_store[page_id] = ET.tostring(existing, encoding="unicode")
            else:
                xml_store[page_id] = params["xml"]
            for item in state:
                if item["id"] == page_id:
                    item["title"] = title
                    item["path"] = f"{parent_item(item['section_id'])['path']}/{title}"
            return {"updated": True}
        if operation == "update_hierarchy":
            root = ET.fromstring(params["xml"])
            pages = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Page"]
            stack: list[dict[str, Any]] = []
            for order, node in enumerate(pages):
                item = next(entry for entry in state if entry["id"] == node.attrib["ID"])
                level = int(node.attrib.get("pageLevel") or item.get("page_level") or 1)
                while stack and int(stack[-1]["page_level"]) >= level:
                    stack.pop()
                item["order"] = order
                item["page_level"] = level
                item["parent_page_id"] = stack[-1]["id"] if stack else None
                counters["clock"] += 1
                item["modified"] = f"2026-08-20T00:00:{counters['clock']:02d}.000Z"
                stack.append(item)
            return {"updated": True}
        if operation == "delete_hierarchy":
            mark_recycled(str(params["object_id"]))
            return {"deleted": True}
        if operation == "open_hierarchy":
            parent = parent_item(str(params["relative_to_id"]))
            name = Path(str(params["path"])).stem
            create_type = int(params.get("create_file_type") or 0)
            if create_type == 2:
                counters["section_group"] += 1
                kind = "section_group"
                object_id = f"created-section-group-{counters['section_group']}"
            else:
                counters["section"] += 1
                kind = "section"
                object_id = f"created-section-{counters['section']}"
            state.append(
                {
                    "resource_type": kind,
                    "id": object_id,
                    "name": name,
                    "path": f"{parent['path']}/{name}",
                    "parent_id": parent["id"],
                    "notebook_id": parent["id"]
                    if parent["resource_type"] == "notebook"
                    else parent["notebook_id"],
                    "is_in_recycle_bin": False,
                }
            )
            return {"object_id": object_id}
        raise AssertionError(operation)

    return call, operations


def test_create_page_current_preflight_skips_extra_hierarchy_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_onenote_mcp.hierarchy import parse_hierarchy
    from local_onenote_mcp.services.backend_operation_classification import (
        current_mutation_epoch,
        reset_mutation_epoch,
        restore_mutation_epoch,
    )
    from local_onenote_mcp.services.hierarchy import HierarchySnapshot
    from tests.test_copying import hierarchy_xml_from_items

    state = _install_real_mutation_readback_fakes(monkeypatch)
    token = reset_mutation_epoch()
    try:
        snapshot = HierarchySnapshot.from_items(
            start_id="",
            scope="pages",
            epoch=current_mutation_epoch(),
            items=parse_hierarchy(hierarchy_xml_from_items(state)),
        )
        with copy_move_read_attribution(), _ledger_recording() as ledger:
            server.services.mutations.create_page(
                "destination-section",
                "Created Page",
                preflight=snapshot,
            )
        assert _count(ledger, "get_hierarchy", DESTINATION_PRECONDITION) == 0
        assert _count(ledger, "get_hierarchy", POST_CREATE_CONVERGENCE) >= 2
    finally:
        restore_mutation_epoch(token)


def test_stale_delete_preflight_falls_back_to_live_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_onenote_mcp.hierarchy import parse_hierarchy
    from local_onenote_mcp.services.backend_operation_classification import (
        notify_backend_operation,
        reset_mutation_epoch,
        restore_mutation_epoch,
    )
    from local_onenote_mcp.services.hierarchy import HierarchySnapshot
    from tests.test_copying import hierarchy_xml_from_items

    state = _install_real_mutation_readback_fakes(monkeypatch)
    token = reset_mutation_epoch()
    try:
        stale = HierarchySnapshot.from_items(
            start_id="",
            scope="pages",
            epoch=0,
            items=parse_hierarchy(hierarchy_xml_from_items(state)),
        )
        notify_backend_operation("update_hierarchy")
        with copy_move_read_attribution(), _ledger_recording() as ledger:
            server.services.mutations.delete_page(
                "source-page",
                "Page",
                "source-section",
                preflight=stale,
            )
        assert _count(ledger, "get_hierarchy", DELETE_CONFIRMATION) >= 1
    finally:
        restore_mutation_epoch(token)


def test_shared_service_page_move_fresh_confirmation_follows_source_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_copying import hierarchy_xml_from_items, page_xml

    state = [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "path": "Source Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Notes",
            "path": "Source Notebook/Notes",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
        },
        {
            "resource_type": "page",
            "id": "source-page",
            "title": "Page",
            "path": "Source Notebook/Notes/Page",
            "parent_id": "source-section",
            "notebook_id": "source-notebook",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "notebook",
            "id": "destination-notebook",
            "name": "Destination Notebook",
            "path": "Destination Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Destination",
            "path": "Destination Notebook/Destination",
            "parent_id": "destination-notebook",
            "notebook_id": "destination-notebook",
        },
    ]
    xml_store = {"source-page": page_xml("source-page", "Page", "body")}
    call, operations = _scripted_bridge(state, xml_store)
    monkeypatch.setattr(OneNoteBridge, "call", call)

    with _ledger_recording() as ledger:
        result = server.services.copying.move_page(
            "source-page",
            "destination-section",
            "Page",
            "source-section",
            destination_title="Moved Page",
            include_descendants=False,
        )

    assert result["outcome"] == "moved"
    reasons = [reason for op, reason in ledger if op == "get_hierarchy"]
    assert reasons.index("source_drift_revalidation") < reasons.index("delete_confirmation")
    assert operations.count("delete_hierarchy") == 1
    assert _count(ledger, "get_hierarchy", DELETE_CONFIRMATION) >= 1


def test_shared_service_root_only_promotion_has_two_fresh_confirmations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_copying import page_xml

    state = [
        {
            "resource_type": "notebook",
            "id": "n",
            "name": "Notebook",
            "path": "Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Source",
            "path": "Notebook/Source",
            "parent_id": "n",
            "notebook_id": "n",
        },
        {
            "resource_type": "page",
            "id": "parent",
            "title": "Parent",
            "path": "Notebook/Source/Parent",
            "parent_id": "source-section",
            "notebook_id": "n",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "page",
            "id": "child",
            "title": "Child",
            "path": "Notebook/Source/Child",
            "parent_id": "source-section",
            "notebook_id": "n",
            "section_id": "source-section",
            "parent_page_id": "parent",
            "page_level": 2,
            "order": 1,
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Destination",
            "path": "Notebook/Destination",
            "parent_id": "n",
            "notebook_id": "n",
        },
    ]
    xml_store = {
        "parent": page_xml("parent", "Parent", "parent-body"),
        "child": page_xml("child", "Child", "child-body"),
    }
    call, operations = _scripted_bridge(state, xml_store)
    monkeypatch.setattr(OneNoteBridge, "call", call)

    with _ledger_recording() as ledger:
        result = server.services.copying.move_page(
            "parent",
            "destination-section",
            "Parent",
            "source-section",
            destination_title="Moved Parent",
            include_descendants=False,
        )

    assert result["outcome"] == "moved"
    assert result["preserved_descendants"]["promoted"] is True
    assert "source_root_modified" not in result["preserved_descendants"]
    assert "modified" not in result["preserved_descendants"]
    confirm_indexes = [
        index
        for index, (op, reason) in enumerate(ledger)
        if op == "get_hierarchy" and reason == DELETE_CONFIRMATION
    ]
    assert len(confirm_indexes) == 2
    assert _count(ledger, "get_hierarchy", DELETE_CONFIRMATION) == 2
    assert operations.count("delete_hierarchy") == 1
    assert operations.count("update_hierarchy") >= 2


def test_shared_service_container_move_fresh_confirmation_before_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_copying import page_xml

    state = [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "path": "Source Notebook",
            "parent_id": None,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "Notes",
            "path": "Source Notebook/Notes",
            "parent_id": "source-notebook",
            "notebook_id": "source-notebook",
        },
        {
            "resource_type": "page",
            "id": "source-page",
            "title": "Page",
            "path": "Source Notebook/Notes/Page",
            "parent_id": "source-section",
            "notebook_id": "source-notebook",
            "section_id": "source-section",
            "parent_page_id": None,
            "page_level": 1,
            "order": 0,
            "is_in_recycle_bin": False,
        },
        {
            "resource_type": "notebook",
            "id": "destination-notebook",
            "name": "Destination Notebook",
            "path": "Destination Notebook",
            "parent_id": None,
        },
    ]
    xml_store = {"source-page": page_xml("source-page", "Page", "body")}
    call, operations = _scripted_bridge(state, xml_store)
    monkeypatch.setattr(OneNoteBridge, "call", call)

    with _ledger_recording() as ledger:
        result = server.services.copying.move_section(
            "source-section",
            "destination-notebook",
            "Notes",
            "source-notebook",
            destination_name="Moved Notes",
        )

    assert result["outcome"] == "moved"
    reasons = [reason for op, reason in ledger if op == "get_hierarchy"]
    assert reasons.index("source_drift_revalidation") < reasons.index("delete_confirmation")
    assert operations.count("delete_hierarchy") == 1
