from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
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


def _replace_saga() -> dict:
    return {
        "state": "applied",
        "execute_attempts": 1,
        "had_backend_error": False,
        "execution_succeeded": True,
    }


def _execution(
    operation: str,
    kind: str,
    backend: str,
    observed_outcome: str,
    *,
    attempts: int = 0,
) -> dict:
    return {
        "operation": operation,
        "stage": "finalize",
        "kind": kind,
        "backend_category": backend,
        "attempts": attempts,
        "replayed": False,
        "backend_calls": 1,
        "completed_steps": [],
        "observed_outcome": observed_outcome,
        "retry_safety": "not_needed",
        "recommended_action": "none",
        "cache_generation": {"before": 1, "after": 1},
        "content_exposed": False,
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


def test_convergence_scenario_exercises_public_control_plane_contracts(
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
            if name == "request_notebook_sync":
                return {
                    "ok": True,
                    "complete": False,
                    "accepted": True,
                    "completion_observable": False,
                    "sync_requested": True,
                    "execution": _execution(
                        name,
                        "lifecycle",
                        "onenote_com",
                        "accepted_completion_unobservable",
                    ),
                }
            if name == "create_notebook":
                target = Path(arguments["base_folder"]) / arguments["name"]
                return {
                    "ok": True,
                    "complete": True,
                    "path": str(target),
                    "notebook_id": "created-notebook-id",
                    "allocated_id": "created-notebook-id",
                    "item": {
                        "id": "created-notebook-id",
                        "resource_type": "notebook",
                        "name": "__operation-runtime-created__",
                        "modified": "created-n1",
                    },
                    "convergence": _convergence(),
                    "reconciliation": _attempt(),
                    "execution": _execution(
                        name,
                        "mutation",
                        "onenote_com",
                        "applied",
                        attempts=1,
                    ),
                }
            if name == "export_object_to_pdf":
                target = Path(arguments["target_path"])
                target.write_bytes(b"fake-pdf")
                return {
                    "ok": True,
                    "complete": True,
                    "path": str(target),
                    "format": "pdf",
                    "execution": _execution(
                        name,
                        "filesystem_effect",
                        "filesystem",
                        "filesystem_effect_completed",
                    ),
                }
            if name == "navigate_to":
                return {
                    "ok": True,
                    "complete": True,
                    "navigated": True,
                    "execution": _execution(
                        name,
                        "ui_effect",
                        "windows_ui",
                        "action_accepted",
                    ),
                }
            if name == "get_hyperlink":
                return {
                    "ok": True,
                    "complete": True,
                    "hyperlink": "onenote:#exact-anchor",
                    "execution": _execution(
                        name,
                        "read",
                        "onenote_com",
                        "completed",
                    ),
                }
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
            if name == "list_page_content_objects":
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
            if name == "rename_page":
                item = {
                    "id": "probe-id",
                    "title": "03-Convergence-Probe-Renamed",
                    "modified": "m2",
                }
            elif name == "replace_page_body":
                item = {
                    "id": "probe-id",
                    "title": "03-Convergence-Probe-Renamed",
                    "modified": "m3",
                }
            elif name == "append_page_content":
                item = {
                    "id": "probe-id",
                    "title": "03-Convergence-Probe-Renamed",
                    "modified": "m4",
                }
            elif name == "reorder_page":
                item = {"id": "probe-id", "order": 1}
            elif name == "delete_page_content_object":
                item = None
            elif name == "delete_page":
                item = None
            elif name == "close_notebook":
                item = {
                    "id": arguments["notebook_id"],
                    "name": arguments["expected_name"],
                }
            else:
                raise AssertionError(f"unexpected tool call: {name}")
            result = {
                "ok": True,
                "complete": True,
                "convergence": _convergence(),
                "reconciliation": _attempt(),
                "execution": _execution(
                    name,
                    "lifecycle" if name == "close_notebook" else "mutation",
                    "onenote_com",
                    "applied",
                    attempts=1,
                ),
            }
            if name == "close_notebook":
                result.update(closed=True, final_state=None)
            if name == "replace_page_body":
                result.update(
                    replaced=True,
                    partial=False,
                    deleted_objects=["base-outline-id"],
                    reconciliation=_replace_saga(),
                )
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
        "request_notebook_sync",
        "create_notebook",
        "close_notebook",
        "export_object_to_pdf",
        "navigate_to",
        "get_hyperlink",
        "create_page",
        "rename_page",
        "replace_page_body",
        "list_page_content_objects",
        "append_page_content",
        "list_page_content_objects",
        "delete_page_content_object",
        "reorder_page",
        "delete_page",
        "close_notebook",
    ]
    delete_content_index = client.calls.index("delete_page_content_object")
    assert (
        client.arguments[delete_content_index]["page_content_object_id"]
        == "fresh-outline-id"
    )
    assert set(result["convergence"]) == {
        "create",
        "title",
        "replace_body",
        "page_update",
        "content_delete",
        "reorder",
        "delete",
        "close",
        "operation_effects",
        "notebook_create_close",
    }
    expected_replace_execution = _execution(
        "replace_page_body",
        "mutation",
        "onenote_com",
        "applied",
        attempts=1,
    )
    expected_replace_execution.pop("completed_steps")
    assert result["convergence"]["replace_body"]["saga"] == {
        "state": "applied",
        "execute_attempts": 1,
        "had_backend_error": False,
        "execution_succeeded": True,
        "partial": False,
        "deleted_object_count": 1,
        "operation_execution": expected_replace_execution,
    }
    assert len(lifecycle.close_results) == 1
    assert result["lifecycle_close_handoff"] == {
        "closed": True,
        "source_notebook_id": "notebook-id",
        "close_origin": "production_close_notebook",
    }
