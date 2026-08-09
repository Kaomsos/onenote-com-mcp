"""Post-mutation worksite preservation contracts for reversible actions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.manual_validation import test_utils
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import move as move_scenario
from tests.manual_validation.scenarios import reconstructive_move_page as reconstructive_scenario
from tests.manual_validation.scenarios import reorder as reorder_scenario
from tests.manual_validation.scenarios.move import MoveScenario
from tests.manual_validation.scenarios.reconstructive_move_page import (
    ReconstructiveMovePageScenario,
)
from tests.manual_validation.scenarios.reorder import ReorderScenario


class FakeClient:
    calls: list[tuple[str, dict]] = []
    response_item: dict = {}

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
        self.calls.append((name, arguments))
        return {"ok": True, "complete": True, "item": self.response_item}


def test_reorder_keep_worksite_skips_restore(monkeypatch, tmp_path) -> None:
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Move-Source",
        "parent_id": "group-a",
    }
    parent = {
        "resource_type": "page",
        "id": "parent-page",
        "name": "Parent",
        "section_id": section["id"],
        "order": 1,
        "page_level": 1,
        "parent_page_id": None,
    }
    target = {
        "resource_type": "page",
        "id": "sibling-page",
        "name": "Sibling",
        "section_id": section["id"],
        "order": 0,
        "page_level": 1,
        "parent_page_id": None,
    }
    changed = {
        **target,
        "order": 1,
        "page_level": 2,
        "parent_page_id": parent["id"],
    }
    changed_parent = {**parent, "order": 0}
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {
            "move_source": section,
            "parent_page": parent,
            "sibling_page": target,
        },
    }
    before = {
        "items": [section, target, parent],
        "page_hashes": {"parent-page": "a", "sibling-page": "b"},
    }
    after = {
        "items": [section, changed_parent, changed],
        "page_hashes": before["page_hashes"],
    }
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    FakeClient.calls = []
    FakeClient.response_item = changed
    monkeypatch.setattr(reorder_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(reorder_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reorder_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        ReorderScenario().execute(
            SimpleNamespace(
                notebook_name=None,
                page_level=2,
                keep_worksite=True,
            ),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    assert [name for name, _ in FakeClient.calls] == ["reorder_page"]
    assert result["worksite_preserved"] is True
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "reorder" / "worksite.json"
    )
    assert worksite["target_ids"] == ["sibling-page"]


def test_move_keep_worksite_skips_restore(monkeypatch, tmp_path) -> None:
    source = {
        "resource_type": "section_group",
        "id": "group-a",
        "name": "Group-A",
        "parent_id": "notebook-id",
    }
    destination = {
        "resource_type": "section_group",
        "id": "group-b",
        "name": "Group-B",
        "parent_id": "notebook-id",
    }
    target = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Move-Source",
        "parent_id": source["id"],
    }
    moved = {**target, "parent_id": destination["id"]}
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {
            "group_a": source,
            "group_b": destination,
            "move_source": target,
        },
    }
    before = {
        "items": [source, destination, target],
        "page_hashes": {"page": "same"},
    }
    after = {
        "items": [source, destination, moved],
        "page_hashes": before["page_hashes"],
    }
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    FakeClient.calls = []
    FakeClient.response_item = moved
    monkeypatch.setattr(move_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(move_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        MoveScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=True),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    assert [name for name, _ in FakeClient.calls] == ["move_section"]
    assert result["worksite_preserved"] is True
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "move" / "worksite.json"
    )
    assert worksite["target_ids"] == ["section-id"]
    assert worksite["current_parent_id"] == "group-b"


def test_reconstructive_move_accepts_active_absence_without_recycle_lookup(
    monkeypatch, tmp_path
) -> None:
    notebook = {"resource_type": "notebook", "id": "notebook", "name": "Notebook"}
    source_section = {
        "resource_type": "section",
        "id": "source-section",
        "name": "Source",
        "parent_id": "notebook",
    }
    destination = {
        "resource_type": "section",
        "id": "destination-section",
        "name": "Destination",
        "parent_id": "notebook",
    }
    source = {
        "resource_type": "page",
        "id": "source-page",
        "title": "Disposable",
        "section_id": "source-section",
        "parent_id": "source-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
        "modified": "before",
    }
    source_child = {
        **source,
        "id": "source-child",
        "title": "List-Tag-Page",
        "parent_page_id": "source-page",
        "page_level": 2,
        "order": 1,
    }
    target = {
        **source,
        "id": "target-page",
        "title": "Moved-Disposable-stamp",
        "section_id": "destination-section",
        "parent_id": "destination-section",
    }
    target_child = {
        **source_child,
        "id": "target-child",
        "section_id": "destination-section",
        "parent_id": "destination-section",
        "parent_page_id": "target-page",
    }
    before = {"items": [notebook, source_section, source, source_child, destination]}
    after = {"items": [notebook, source_section, destination, target, target_child]}
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    class FakeReconstructiveClient:
        policy = reconstructive_scenario.RECONSTRUCTIVE_MOVE_PAGE_POLICY
        allowed_tools = reconstructive_scenario.RECONSTRUCTIVE_MOVE_PAGE_TOOLS | {
            "health_check"
        }
        timeout_seconds = 1_800

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append(name)
            if name == "plan_reconstructive_move_page":
                return {
                    "plan_digest": "digest",
                    "content_capabilities": [
                        "Image",
                        "List",
                        "Outline",
                        "RichText",
                        "Table",
                        "Tag",
                    ],
                }
            assert name == "reconstructive_move_page"
            return {
                "item": target,
                "created_ids": ["target-page", "target-child"],
                "copy_report": {
                    "verified": True,
                    "lossless": True,
                    "id_map": {
                        "source-page": "target-page",
                        "source-child": "target-child",
                    },
                },
                "source_deleted_nonpermanently": True,
                "recycle_bin_verification": "not_required_com_unavailable",
                "recycled_source_ids": [],
                "recycle_unverified_source_ids": ["source-child", "source-page"],
            }

    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "disposable_page": source,
            "move_source": destination,
        },
    }
    client = FakeReconstructiveClient()
    monkeypatch.setattr(reconstructive_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reconstructive_scenario, "timestamp", lambda: "stamp")
    monkeypatch.setattr(reconstructive_scenario, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        ReconstructiveMovePageScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=True),
            RuntimeOptions(tmp_path, 1_800, False, False),
            manifest,
            client=client,
            fixture_result={},
        )
    )

    assert client.calls == [
        "plan_reconstructive_move_page",
        "reconstructive_move_page",
    ]
    assert result["status"] == "passed"
    assert result["source_deleted_nonpermanently"] is True
    assert result["recycle_bin_verification"] == "not_required_com_unavailable"
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "reconstructive-move-page" / "worksite.json"
    )
    assert worksite["source_ids"] == ["source-child", "source-page"]
