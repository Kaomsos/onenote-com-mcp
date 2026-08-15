from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import onenote_convergence as convergence_runtime
from tests.manual_validation.scenarios.onenote_convergence import (
    OneNoteConvergenceScenario,
)


def _convergence() -> dict:
    return {
        "converged": True,
        "attempts": 2,
        "elapsed_seconds": 0.01,
        "stable_observations": 2,
        "identity_remap": {},
        "transient_errors": [],
    }


def _attempt() -> dict:
    return {
        "state": "applied",
        "execute_attempts": 1,
        "had_backend_error": False,
        "execution_succeeded": True,
        "mutation_stage": "postcondition",
        "preflight_state": "logical_ready",
        "persistence_checkpoint": "not_observable",
        "mutation_attempted": True,
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "applied",
        "execute_error_reconciled": False,
        "retry_safety": "not_needed",
        "recommended_action": "none",
        "manual_recovery_required": False,
        "observation_attempts": 1,
        "identity_policy": "preserved",
    }


def _snapshot(*, include_probe: bool = False, probe_order: int = 2) -> dict:
    items = [
        {
            "id": "notebook-id",
            "resource_type": "notebook",
            "name": "Disposable",
            "modified": "n1",
            "parent_id": None,
        },
        {
            "id": "section-id",
            "resource_type": "section",
            "name": "01-Convergence-Section",
            "parent_id": "notebook-id",
        },
        {
            "id": "first-id",
            "resource_type": "page",
            "title": "01-Anchor",
            "section_id": "section-id",
            "parent_id": "section-id",
            "order": 0,
            "page_level": 1,
        },
        {
            "id": "second-id",
            "resource_type": "page",
            "title": "02-Anchor",
            "section_id": "section-id",
            "parent_id": "section-id",
            "order": 1,
            "page_level": 1,
        },
    ]
    if include_probe:
        items.append(
            {
                "id": "probe-id",
                "resource_type": "page",
                "title": "03-Convergence-Probe-Renamed",
                "section_id": "section-id",
                "parent_id": "section-id",
                "modified": "m4",
                "order": probe_order,
                "page_level": 1,
            }
        )
    return {
        "items": items,
        "page_hashes": {"first-id": "h1", "second-id": "h2"},
        "page_objects": {"first-id": [], "second-id": []},
    }


def test_convergence_scenario_exercises_missing_public_attempt_contracts(
    monkeypatch, tmp_path
) -> None:
    before = _snapshot()
    snapshots = iter(
        [
            before,
            _snapshot(include_probe=True),
            _snapshot(include_probe=True, probe_order=1),
            deepcopy(before),
        ]
    )

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(convergence_runtime, "capture_snapshot", fake_snapshot)

    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.arguments: list[dict] = []
            self.object_reads = 0

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append(name)
            self.arguments.append(dict(arguments))
            if name == "create_page":
                return {
                    "page_id": "probe-id",
                    "allocated_id": "probe-id",
                    "page": {
                        "id": "probe-id",
                        "title": "03-Convergence-Probe",
                        "modified": "m1",
                    },
                    "convergence": _convergence(),
                }
            if name == "get_page_objects":
                self.object_reads += 1
                objects = [
                    {
                        "id": "base-outline-id",
                        "kind": "Outline",
                        "can_delete": True,
                        "delete_target_id": "base-outline-id",
                    },
                    {
                        "id": "base-oe-id",
                        "kind": "OE",
                        "can_delete": False,
                        "delete_target_id": "base-outline-id",
                    },
                ]
                if self.object_reads == 2:
                    objects.extend(
                        [
                            {
                                "id": "fresh-outline-id",
                                "kind": "Outline",
                                "can_delete": True,
                                "delete_target_id": "fresh-outline-id",
                            },
                            {
                                "id": "fresh-oe-id",
                                "kind": "OE",
                                "can_delete": False,
                                "delete_target_id": "fresh-outline-id",
                            },
                        ]
                    )
                return {"objects": objects, "count": len(objects)}
            if name == "update_page_title":
                item = {
                    "id": "probe-id",
                    "title": "03-Convergence-Probe-Renamed",
                    "modified": "m2",
                }
            elif name == "append_to_page":
                item = {
                    "id": "probe-id",
                    "title": "03-Convergence-Probe-Renamed",
                    "modified": "m3",
                }
            elif name == "reorder_page":
                item = {"id": "probe-id", "order": 1}
            elif name == "delete_page_content":
                item = None
            elif name == "delete_page":
                item = None
            elif name == "close_notebook":
                item = {"id": "notebook-id", "name": "Disposable"}
            else:
                raise AssertionError(f"unexpected tool call: {name}")
            result = {
                "ok": True,
                "complete": True,
                "convergence": _convergence(),
                "reconciliation": _attempt(),
            }
            if name == "close_notebook":
                result.update(closed=True, final_state=None)
            if item is not None:
                result["item"] = item
            return result

    class Lifecycle:
        def __init__(self) -> None:
            self.close_results: list[dict] = []

        def adopt_production_close(self, close_result: dict) -> dict:
            self.close_results.append(deepcopy(close_result))
            return {
                "closed": True,
                "source_notebook_id": "notebook-id",
                "close_origin": "production_close_notebook",
            }

    manifest = {
        "notebook": {"id": "notebook-id", "name": "Disposable"},
        "structure": {
            "convergence_section": {"id": "section-id"},
            "first_anchor_page": {"id": "first-id", "order": 0},
        },
    }
    client = Client()
    lifecycle = Lifecycle()
    result = asyncio.run(
        OneNoteConvergenceScenario().execute_with_lifecycle(
            SimpleNamespace(notebook_name=None, keep_worksite=False),
            RuntimeOptions(tmp_path, 300, False, False),
            manifest,
            client=client,
            fixture_result={},
            wrappers={"source": lifecycle},
        )
    )

    assert result["status"] == "passed"
    assert client.calls == [
        "create_page",
        "update_page_title",
        "get_page_objects",
        "append_to_page",
        "get_page_objects",
        "delete_page_content",
        "reorder_page",
        "delete_page",
        "close_notebook",
    ]
    delete_content_index = client.calls.index("delete_page_content")
    assert client.arguments[delete_content_index]["object_id"] == "fresh-outline-id"
    assert set(result["convergence"]) == {
        "create",
        "title",
        "page_update",
        "content_delete",
        "reorder",
        "delete",
        "close",
    }
    assert len(lifecycle.close_results) == 1
    assert result["lifecycle_close_handoff"] == {
        "closed": True,
        "source_notebook_id": "notebook-id",
        "close_origin": "production_close_notebook",
    }
