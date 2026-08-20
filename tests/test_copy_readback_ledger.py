"""Deterministic Copy/Move readback ledger and phase-local snapshot budgets."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from local_onenote_mcp import server
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
    install_recursive_execute_fakes,
)


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
    ("get_hierarchy", "topology_verification"): 3,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
}
_MOVE_PAGE_BUDGET: dict[tuple[str, str | None], int] = {
    ("get_hierarchy", "delete_convergence"): 1,
    ("get_hierarchy", "source_confirmation"): 1,
    ("get_hierarchy", "source_drift_revalidation"): 1,
    ("get_hierarchy", "topology_verification"): 3,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
    ("get_page_content", "source_drift_revalidation"): 1,
}
_CONTAINER_COPY_BUDGET: dict[tuple[str, str | None], int] = dict(_COPY_PAGE_BUDGET)
_MOVE_CONTAINER_BUDGET: dict[tuple[str, str | None], int] = {
    ("get_hierarchy", "delete_convergence"): 1,
    ("get_hierarchy", "source_confirmation"): 1,
    ("get_hierarchy", "source_drift_revalidation"): 1,
    ("get_hierarchy", "topology_verification"): 3,
    ("get_page_content", "delete_convergence"): 1,
    ("get_page_content", "plan_capture"): 1,
    ("get_page_content", "post_write_reconciliation"): 1,
    ("get_page_content", "pre_write_target_observation"): 1,
    ("get_page_content", "source_drift_revalidation"): 2,
}


@pytest.fixture(autouse=True)
def _enable_copy_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE", "true")
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
