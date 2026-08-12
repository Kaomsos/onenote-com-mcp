"""Pure contracts for the cross-Notebook container Move scenario executors."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import container_move_scenario
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.destination_position import (
    expected_destination_position,
)


@pytest.mark.parametrize(
    ("scenario_name", "resource_type"),
    [("move-section", "section"), ("move-section-group", "section_group")],
)
def test_container_move_scenario_checks_verified_copy_and_one_root_delete(
    monkeypatch, tmp_path, scenario_name, resource_type
):
    source_root_id = "source-section" if resource_type == "section" else "source-group"
    source_items = [
        {
            "resource_type": "notebook",
            "id": "source-notebook",
            "name": "Source Notebook",
            "parent_id": None,
        }
    ]
    if resource_type == "section_group":
        source_items.append(
            {
                "resource_type": "section_group",
                "id": "source-group",
                "name": "Source Group",
                "parent_id": "source-notebook",
                "notebook_id": "source-notebook",
            }
        )
        section_parent = "source-group"
    else:
        section_parent = "source-notebook"
    source_items.extend(
        [
            {
                "resource_type": "section",
                "id": "source-section",
                "name": "Source Section",
                "parent_id": section_parent,
                "notebook_id": "source-notebook",
            },
            {
                "resource_type": "page",
                "id": "source-page",
                "title": "Source Page",
                "parent_id": "source-section",
                "section_id": "source-section",
                "notebook_id": "source-notebook",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
        ]
    )
    state = {
        "source": source_items,
        "destination": [
            {
                "resource_type": "notebook",
                "id": "destination-notebook",
                "name": "Destination Notebook",
                "parent_id": None,
            }
        ],
    }

    async def fake_snapshot(_client, notebook_id):
        role = "source" if notebook_id == "source-notebook" else "destination"
        pages = [item for item in state[role] if item["resource_type"] == "page"]
        return {
            "notebook_id": notebook_id,
            "items": [dict(item) for item in state[role]],
            "page_hashes": {item["id"]: f"hash-{item['id']}" for item in pages},
            "page_objects": {item["id"]: [] for item in pages},
        }

    class FakeClient:
        async def call_tool(self, name, arguments):
            source_subtree = [
                item for item in state["source"] if item["id"] != "source-notebook"
            ]
            source_ids = [item["id"] for item in source_subtree]
            id_map = {value: f"target-{value}" for value in source_ids}
            if name.startswith("plan_move_"):
                return {
                    "operation": f"move_{resource_type}",
                    "plan_digest": "digest",
                    "move_notebooks": {
                        "source_notebook_id": "source-notebook",
                        "destination_notebook_id": "destination-notebook",
                        "cross_notebook": True,
                    },
                    "snapshots": {"source": {"resources": source_subtree}},
                }
            assert name == f"move_{resource_type}"
            destination_name = arguments["destination_name"]
            for item in source_subtree:
                target = {**item, "id": id_map[item["id"]], "notebook_id": "destination-notebook"}
                if item["id"] == source_root_id:
                    target["name"] = destination_name
                    target["parent_id"] = "destination-notebook"
                elif item["resource_type"] in {"section", "section_group"}:
                    target["parent_id"] = id_map[item["parent_id"]]
                else:
                    target["parent_id"] = id_map[item["section_id"]]
                    target["section_id"] = id_map[item["section_id"]]
                state["destination"].append(target)
            state["source"] = [state["source"][0]]
            response = {
                "copy_report": {
                    "verified": True,
                    "lossless": True,
                    "id_map": id_map,
                },
                "source_deleted_nonpermanently": True,
                "attempted_source_ids": [source_root_id],
                "deleted_source_ids": [source_root_id],
                "inactive_source_ids": source_ids,
                "remaining_source_ids": [],
            }
            response["destination_position"] = expected_destination_position(
                {"items": [*state["source"], *state["destination"]]},
                id_map[source_root_id],
            )
            return response

    fake_client = FakeClient()

    @asynccontextmanager
    async def fake_scenario_client(*_args, **_kwargs):
        yield fake_client

    monkeypatch.setattr(container_move_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(container_move_scenario, "scenario_client", fake_scenario_client)
    monkeypatch.setattr(container_move_scenario, "run_safe_timestamp", lambda _args: "stamp")
    monkeypatch.setattr(container_move_scenario, "render_report", lambda _run_dir: None)
    scenario = SCENARIO_REGISTRY.get(scenario_name)
    manifest = {
        "notebook": {"id": "source-notebook", "name": "Source Notebook"},
        "notebooks": {
            "source": {"id": "source-notebook", "name": "Source Notebook"},
            "destination": {
                "id": "destination-notebook",
                "name": "Destination Notebook",
            },
        },
        "structure": {scenario.spec.execution_contract["source_key"]: source_items[1]},
    }
    if resource_type == "section":
        manifest["structure"]["source_section"] = source_items[1]

    result = asyncio.run(
        scenario.execute(
            SimpleNamespace(notebook_name="Source Notebook", keep_worksite=False),
            RuntimeOptions(tmp_path, 1_800, False, False),
            manifest,
            client=fake_client,
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert result["source_deleted_nonpermanently"] is True
    assert result["source_ids"][0] == source_root_id
    assert all(value.startswith("target-") for value in result["target_ids"])
