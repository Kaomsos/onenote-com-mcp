"""Post-mutation worksite preservation contracts for reversible actions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.manual_validation import test_utils
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios import create as create_scenario
from tests.manual_validation.scenarios import move_page as move_page_scenario
from tests.manual_validation.scenarios import reparent_section as reparent_section_scenario
from tests.manual_validation.scenarios import reorder_page as reorder_page_scenario
from tests.manual_validation.scenarios.move_page import MovePageScenario
from tests.manual_validation.scenarios.create import CreateScenario
from tests.manual_validation.scenarios.reparent_section import ReparentSectionScenario
from tests.manual_validation.scenarios.reorder_page import ReorderPageScenario


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


def test_create_duplicate_title_regression_uses_fresh_ids_and_exact_cleanup(
    monkeypatch, tmp_path
) -> None:
    notebook = {"resource_type": "notebook", "id": "notebook", "name": "Notebook"}
    section = {
        "resource_type": "section",
        "id": "duplicate-section",
        "name": "Duplicate-Title-Target",
        "parent_id": "notebook",
    }
    pages = [
        {
            "resource_type": "page",
            "id": f"page-{ordinal}",
            "title": "Duplicate-Title-Regression",
            "section_id": section["id"],
            "parent_id": section["id"],
            "page_level": 1,
            "order": ordinal - 1,
            "modified": f"m-{ordinal}",
        }
        for ordinal in (1, 2)
    ]
    before = {
        "items": [notebook, section],
        "page_hashes": {},
        "page_objects": {},
    }
    after = {
        "items": [notebook, section, *pages],
        "page_hashes": {"page-1": "hash-1", "page-2": "hash-2"},
        "page_objects": {"page-1": [], "page-2": []},
    }
    snapshots = iter([before, after, before])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    class CreateClient:
        def __init__(self) -> None:
            self.create_count = 0
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            if name == "create_page":
                self.create_count += 1
                page_id = f"page-{self.create_count}"
                return {
                    "page_id": page_id,
                    "allocated_id": page_id,
                    "identity_remapped": False,
                    "page": pages[self.create_count - 1],
                }
            assert name == "delete_page"
            return {"deleted": True, "object_id": arguments["page_id"]}

    monkeypatch.setattr(create_scenario, "capture_snapshot", fake_snapshot)
    client = CreateClient()
    result = asyncio.run(
        CreateScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=False),
            RuntimeOptions(tmp_path, 1_800, False, False),
            {
                "schema_version": 1,
                "notebook": notebook,
                "structure": {"duplicate_title_section": section},
            },
            client=client,
            fixture_result={},
        )
    )

    assert result["duplicate_title_regression"]["fresh_and_distinct"] is True
    assert result["restored"] is True
    delete_calls = [arguments for name, arguments in client.calls if name == "delete_page"]
    assert [call["page_id"] for call in delete_calls] == ["page-2", "page-1"]
    assert all(call["permanently"] is False for call in delete_calls)


def test_reorder_keep_worksite_skips_restore(monkeypatch, tmp_path) -> None:
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "01-Reorder-Page-Section",
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
            "reorder_section": section,
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
    monkeypatch.setattr(reorder_page_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(reorder_page_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reorder_page_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        ReorderPageScenario().execute(
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
        tmp_path / "scenarios" / "reorder-page" / "worksite.json"
    )
    assert worksite["target_ids"] == ["sibling-page"]


def test_reparent_section_keep_worksite_skips_restore(monkeypatch, tmp_path) -> None:
    notebook = {
        "resource_type": "notebook",
        "id": "notebook-id",
        "name": "Notebook",
    }
    groups = {
        key: {
            "resource_type": "section_group",
            "id": key,
            "name": key,
            "parent_id": notebook["id"],
        }
        for key in ("destination-1", "source-2", "source-3", "destination-3")
    }
    sections = {
        "section-1": {
            "resource_type": "section",
            "id": "section-1",
            "name": "01-Notebook-To-Group-Section",
            "parent_id": notebook["id"],
        },
        "section-2": {
            "resource_type": "section",
            "id": "section-2",
            "name": "02-Group-To-Notebook-Section",
            "parent_id": groups["source-2"]["id"],
        },
        "section-3": {
            "resource_type": "section",
            "id": "section-3",
            "name": "03-Group-To-Group-Section",
            "parent_id": groups["source-3"]["id"],
        },
    }
    pages = [
        {
            "resource_type": "page",
            "id": f"page-{index}",
            "title": f"0{index}-Page",
            "section_id": f"section-{index}",
            "parent_id": f"section-{index}",
            "order": 0,
            "page_level": 1,
            "parent_page_id": None,
        }
        for index in (1, 2, 3)
    ]
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "notebook_to_group_destination": groups["destination-1"],
            "notebook_to_group_section": sections["section-1"],
            "group_to_notebook_source": groups["source-2"],
            "group_to_notebook_section": sections["section-2"],
            "group_to_group_source": groups["source-3"],
            "group_to_group_destination": groups["destination-3"],
            "group_to_group_section": sections["section-3"],
        },
    }
    base_items = [notebook, *groups.values(), *sections.values(), *pages]
    before = {
        "items": base_items,
        "page_hashes": {f"page-{index}": f"hash-{index}" for index in (1, 2, 3)},
    }

    def moved_snapshot(**parents):
        return {
            "items": [
                *[notebook, *groups.values()],
                *[
                    {**section, "parent_id": parents.get(section_id, section["parent_id"])}
                    for section_id, section in sections.items()
                ],
                *pages,
            ],
            "page_hashes": before["page_hashes"],
        }

    after_1 = moved_snapshot(**{"section-1": groups["destination-1"]["id"]})
    after_2 = moved_snapshot(
        **{
            "section-1": groups["destination-1"]["id"],
            "section-2": notebook["id"],
        }
    )
    after_3 = moved_snapshot(
        **{
            "section-1": groups["destination-1"]["id"],
            "section-2": notebook["id"],
            "section-3": groups["destination-3"]["id"],
        }
    )
    snapshots = iter([before, after_1, after_2, after_3])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    FakeClient.calls = []
    monkeypatch.setattr(reparent_section_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(reparent_section_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reparent_section_scenario, "render_report", lambda _run_dir: None)
    result = asyncio.run(
        ReparentSectionScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=True),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    assert [name for name, _ in FakeClient.calls] == ["reparent_section"] * 3
    assert result["worksite_preserved"] is True
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "reparent-section" / "worksite.json"
    )
    assert worksite["target_ids"] == ["section-1", "section-2", "section-3"]
    assert [operation["case"] for operation in worksite["operations"]] == [
        "notebook-to-section-group",
        "section-group-to-notebook",
        "section-group-to-section-group",
    ]
    assert [operation["destination_parent_id"] for operation in worksite["operations"]] == [
        "destination-1",
        "notebook-id",
        "destination-3",
    ]


def test_reparent_section_default_restores_three_cases_in_reverse_order(monkeypatch, tmp_path) -> None:
    notebook = {"id": "notebook", "resource_type": "notebook", "name": "Notebook"}
    groups = {
        key: {
            "id": key,
            "resource_type": "section_group",
            "name": key,
            "parent_id": "notebook",
        }
        for key in ("destination-1", "source-2", "source-3", "destination-3")
    }
    sections = {
        "section-1": {
            "id": "section-1",
            "resource_type": "section",
            "name": "01-Notebook-To-Group-Section",
            "parent_id": "notebook",
        },
        "section-2": {
            "id": "section-2",
            "resource_type": "section",
            "name": "02-Group-To-Notebook-Section",
            "parent_id": "source-2",
        },
        "section-3": {
            "id": "section-3",
            "resource_type": "section",
            "name": "03-Group-To-Group-Section",
            "parent_id": "source-3",
        },
    }

    def snapshot(parent_1: str, parent_2: str, parent_3: str) -> dict:
        return {
            "items": [
                notebook,
                *groups.values(),
                {**sections["section-1"], "parent_id": parent_1},
                {**sections["section-2"], "parent_id": parent_2},
                {**sections["section-3"], "parent_id": parent_3},
            ],
            "page_hashes": {},
        }

    before = snapshot("notebook", "source-2", "source-3")
    snapshots = iter(
        [
            before,
            snapshot("destination-1", "source-2", "source-3"),
            snapshot("destination-1", "notebook", "source-3"),
            snapshot("destination-1", "notebook", "destination-3"),
            snapshot("destination-1", "notebook", "source-3"),
            snapshot("destination-1", "source-2", "source-3"),
            before,
        ]
    )

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "notebook_to_group_destination": groups["destination-1"],
            "notebook_to_group_section": sections["section-1"],
            "group_to_notebook_source": groups["source-2"],
            "group_to_notebook_section": sections["section-2"],
            "group_to_group_source": groups["source-3"],
            "group_to_group_destination": groups["destination-3"],
            "group_to_group_section": sections["section-3"],
        },
    }
    FakeClient.calls = []
    monkeypatch.setattr(reparent_section_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(reparent_section_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reparent_section_scenario, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        ReparentSectionScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=False),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    reparent_calls = [arguments for name, arguments in FakeClient.calls if name == "reparent_section"]
    assert [call["section_id"] for call in reparent_calls] == [
        "section-1",
        "section-2",
        "section-3",
        "section-3",
        "section-2",
        "section-1",
    ]
    assert [call["destination_parent_id"] for call in reparent_calls] == [
        "destination-1",
        "notebook",
        "destination-3",
        "source-3",
        "source-2",
        "notebook",
    ]
    assert result["restored"] is True
    assert (tmp_path / "scenarios" / "reparent-section" / "restored.json").exists()


def test_move_page_accepts_active_absence_without_recycle_lookup(
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
    anchor = {
        **source_child,
        "id": "collision-anchor",
        "section_id": "destination-section",
        "parent_id": "destination-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
    }
    before = {
        "items": [notebook, source_section, source, source_child, destination, anchor],
        "page_hashes": {"collision-anchor": "anchor-hash"},
    }
    after = {
        "items": [notebook, source_section, destination, anchor, target, target_child],
        "page_hashes": {"collision-anchor": "anchor-hash"},
    }
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    class FakeMovePageClient:
        policy = move_page_scenario.MOVE_PAGE_POLICY
        allowed_tools = move_page_scenario.MOVE_PAGE_TOOLS | {
            "health_check"
        }
        timeout_seconds = 1_800

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append(name)
            if name == "plan_move_page":
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
            assert name == "move_page"
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
                "attempted_source_ids": ["source-child", "source-page"],
            }

    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "disposable_page": source,
            "destination_section": destination,
            "collision_anchor": anchor,
        },
    }
    client = FakeMovePageClient()
    monkeypatch.setattr(move_page_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_page_scenario, "run_safe_timestamp", lambda _args: "stamp")
    monkeypatch.setattr(move_page_scenario, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        MovePageScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=True),
            RuntimeOptions(tmp_path, 1_800, False, False),
            manifest,
            client=client,
            fixture_result={},
        )
    )

    assert client.calls == [
        "plan_move_page",
        "move_page",
    ]
    assert result["status"] == "passed"
    assert result["source_deleted_nonpermanently"] is True
    assert result["recycle_bin_verification"] == "not_required_com_unavailable"
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "move-page" / "worksite.json"
    )
    assert worksite["source_ids"] == ["source-child", "source-page"]
