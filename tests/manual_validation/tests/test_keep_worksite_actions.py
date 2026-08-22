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
from tests.manual_validation.scenarios.common.destination_position import (
    expected_destination_position,
)
from tests.manual_validation.scenarios.common.page_readback import SPECIAL_PAGE_TITLE


class FakeClient:
    calls: list[tuple[str, dict]] = []
    response_item: dict = {}
    last_response: dict | None = None

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
        self.calls.append((name, arguments))
        inner = {"ok": True, "complete": True, "item": self.response_item}
        if arguments.get("items"):
            batch_item = arguments["items"][0]
            target_id = (
                batch_item.get("page_id")
                or batch_item.get("section_id")
                or batch_item.get("section_group_id")
            )
            inner["id_map"] = {target_id: target_id}
            self.last_response = inner
            return {
                "operation": name,
                "mode": "batch",
                "items": [{"input_index": 0, "object_id": target_id, "status": "applied", "result": inner}],
                "final_hierarchy": {
                    "destination_parent_id": arguments.get("destination_section_id")
                    or arguments.get("destination_parent_id"),
                    "item_count": 1,
                    "items": [{"input_index": 0, "current_id": target_id}],
                    "verification_scope": {"page_content": "not_read"},
                },
            }
        self.last_response = inner
        return inner


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
    page_ids = [
        "{01234567-89AB-CDEF-0123-456789ABCDEF}{1}{E1001}",
        "{89ABCDEF-0123-4567-89AB-CDEF01234567}{1}{E1002}",
    ]
    pages = [
        {
            "resource_type": "page",
            "id": page_ids[ordinal - 1],
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
        "page_hashes": dict(zip(page_ids, ("hash-1", "hash-2"), strict=True)),
        "page_objects": {page_id: [] for page_id in page_ids},
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
                page_id = page_ids[self.create_count - 1]
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
    assert [call["page_id"] for call in delete_calls] == list(reversed(page_ids))
    assert all(call["permanently"] is False for call in delete_calls)
    evidence_root = tmp_path / "scenarios" / "create"
    assert {path.name for path in evidence_root.glob("cleanup-*.json")} == {
        "cleanup-created-page-01-result.json",
        "cleanup-created-page-02-result.json",
    }
    create_results = test_utils.read_json(evidence_root / "create-results.json")
    assert [item["page_id"] for item in create_results["created"]] == page_ids


def test_create_default_records_safe_batch_rejection_before_valid_batches(
    monkeypatch, tmp_path
) -> None:
    notebook = {
        "resource_type": "notebook",
        "id": "notebook",
        "name": "Notebook",
        "modified": "notebook-before",
    }
    section = {
        "resource_type": "section",
        "id": "duplicate-section",
        "name": "Duplicate-Title-Target",
        "parent_id": "notebook",
        "modified": "section-before",
    }
    duplicate_pages = [
        {
            "resource_type": "page",
            "id": f"duplicate-page-{index}",
            "title": "Duplicate-Title-Regression",
            "section_id": section["id"],
            "parent_id": section["id"],
            "page_level": 1,
            "parent_page_id": None,
            "order": index,
            "modified": f"duplicate-{index}",
        }
        for index in range(2)
    ]
    batch_targets = {
        "create_section_group": [
            {
                "resource_type": "section_group",
                "id": f"batch-group-{index}",
                "name": f"Batch-Group-{'AB'[index]}",
                "parent_id": notebook["id"],
                "modified": f"group-{index}",
            }
            for index in range(2)
        ],
        "create_section": [
            {
                "resource_type": "section",
                "id": f"batch-section-{index}",
                "name": f"Batch-Section-{'AB'[index]}",
                "parent_id": notebook["id"],
                "modified": f"section-{index}",
            }
            for index in range(2)
        ],
        "create_page": [
            {
                "resource_type": "page",
                "id": f"batch-page-{index}",
                "title": f"Batch-Page-{'AB'[index]}",
                "section_id": section["id"],
                "parent_id": section["id"],
                "page_level": 1,
                "parent_page_id": None,
                "order": index + 2,
                "modified": f"page-{index}",
            }
            for index in range(2)
        ],
    }
    before = {"items": [notebook, section], "page_hashes": {}, "page_objects": {}}
    after = {
        "items": [notebook, section, *duplicate_pages],
        "page_hashes": {
            "duplicate-page-0": "duplicate-hash-0",
            "duplicate-page-1": "duplicate-hash-1",
        },
        "page_objects": {"duplicate-page-0": [], "duplicate-page-1": []},
    }
    batch_after = {
        "items": [
            *after["items"],
            *batch_targets["create_section_group"],
            *batch_targets["create_section"],
            *batch_targets["create_page"],
        ],
        "page_hashes": after["page_hashes"],
        "page_objects": after["page_objects"],
    }
    snapshots = iter([before, after, after, batch_after, before])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    rejection_calls: list[dict] = []

    async def fake_expected_rejection(
        _client, tool_name, arguments, evidence_path, *, label, expected_message_fragment
    ):
        rejection_calls.append(arguments)
        evidence = {
            "tool": tool_name,
            "label": label,
            "expected_message_fragment": expected_message_fragment,
            "mutation_attempted": False,
        }
        test_utils.write_json(evidence_path, evidence)
        return evidence

    class CreateBatchClient:
        def __init__(self) -> None:
            self.single_page_index = 0
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            if arguments.get("items"):
                targets = batch_targets[name]
                return {
                    "operation": name,
                    "mode": "batch",
                    "applied_count": 2,
                    "partial": False,
                    "items": [
                        {
                            "input_index": index,
                            "object_id": target["id"],
                            "status": "applied",
                            "result": {"item": target},
                        }
                        for index, target in enumerate(targets)
                    ],
                }
            if name == "create_page":
                page = duplicate_pages[self.single_page_index]
                self.single_page_index += 1
                return {
                    "page_id": page["id"],
                    "allocated_id": page["id"],
                    "identity_remapped": False,
                    "page": page,
                }
            assert name.startswith("delete_")
            return {"deleted": True, "permanently": False}

    monkeypatch.setattr(create_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(
        create_scenario,
        "expect_mutation_preflight_rejection",
        fake_expected_rejection,
    )
    client = CreateBatchClient()
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
            fixture_result={"status": "passed"},
        )
    )

    assert rejection_calls[0]["items"] == [
        {"name": "Rejected-Duplicate"},
        {"name": "rejected-duplicate.one"},
    ]
    assert result["expected_rejection"]["mutation_attempted"] is False
    assert all(value["applied_count"] == 2 for value in result["batch_results"].values())
    assert len(result["cleanup_results"]) == 8
    assert result["restored"] is True
    assert (
        tmp_path / "scenarios" / "create" / "expected-batch-rejection.json"
    ).exists()


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
        "order": 0,
        "page_level": 1,
        "parent_page_id": None,
    }
    target = {
        "resource_type": "page",
        "id": "sibling-page",
        "name": "Sibling",
        "section_id": section["id"],
        "order": 2,
        "page_level": 1,
        "parent_page_id": None,
    }
    child = {
        **parent,
        "id": "child-page",
        "name": "Child",
        "order": 1,
        "page_level": 2,
        "parent_page_id": parent["id"],
    }
    changed = {
        **target,
        "order": 1,
        "page_level": 2,
        "parent_page_id": parent["id"],
    }
    changed_parent = {**parent, "order": 0}
    changed_child = {**child, "order": 2}
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {
            "reorder_section": section,
            "parent_page": parent,
            "child_page": child,
            "sibling_page": target,
        },
    }
    before = {
        "items": [section, parent, child, target],
        "page_hashes": {"parent-page": "a", "child-page": "c", "sibling-page": "b"},
    }
    root_only_after = {
        "items": [
            section,
            {**child, "order": 0, "page_level": 1, "parent_page_id": None},
            {**target, "order": 1},
            {**parent, "order": 2},
        ],
        "page_hashes": before["page_hashes"],
    }
    root_before_child_restore = {
        "items": [
            section,
            parent,
            {**child, "page_level": 1, "parent_page_id": None},
            target,
        ],
        "page_hashes": before["page_hashes"],
    }
    subtree_after = {
        "items": [section, {**target, "order": 0}, {**parent, "order": 1}, {**child, "order": 2}],
        "page_hashes": before["page_hashes"],
    }
    after = {
        "items": [section, changed_parent, changed, changed_child],
        "page_hashes": before["page_hashes"],
    }
    snapshots = iter([
        before,
        root_only_after,
        root_before_child_restore,
        before,
        subtree_after,
        before,
        after,
    ])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    FakeClient.calls = []
    FakeClient.last_response = None
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

    assert [name for name, _ in FakeClient.calls] == ["reorder_page"] * 6
    assert result["worksite_preserved"] is True
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "reorder-page" / "worksite.json"
    )
    assert worksite["target_ids"] == ["sibling-page"]


def test_reorder_default_records_safe_sort_rejection_and_restores(
    monkeypatch, tmp_path
) -> None:
    section = {
        "resource_type": "section",
        "id": "section-id",
        "name": "01-Reorder-Page-Section",
        "parent_id": "notebook-id",
    }
    parent = {
        "resource_type": "page",
        "id": "parent-page",
        "title": "01-Parent",
        "section_id": section["id"],
        "parent_id": section["id"],
        "order": 0,
        "page_level": 1,
        "parent_page_id": None,
        "modified": "parent-before",
    }
    target = {
        "resource_type": "page",
        "id": "sibling-page",
        "title": "03-Sibling",
        "section_id": section["id"],
        "parent_id": section["id"],
        "order": 2,
        "page_level": 1,
        "parent_page_id": None,
        "modified": "target-before",
    }
    child = {
        **parent,
        "id": "child-page",
        "title": "02-Child",
        "order": 1,
        "page_level": 2,
        "parent_page_id": parent["id"],
        "modified": "child-before",
    }
    changed_parent = {**parent, "order": 0, "modified": "parent-after"}
    changed = {
        **target,
        "order": 1,
        "page_level": 2,
        "parent_page_id": parent["id"],
        "modified": "target-after",
    }
    changed_child = {**child, "order": 2}
    before = {
        "items": [section, parent, child, target],
        "page_hashes": {"parent-page": "a", "child-page": "c", "sibling-page": "b"},
    }
    root_only_after = {
        "items": [
            section,
            {**child, "order": 0, "page_level": 1, "parent_page_id": None},
            {**target, "order": 1},
            {**parent, "order": 2},
        ],
        "page_hashes": before["page_hashes"],
    }
    root_before_child_restore = {
        "items": [
            section,
            parent,
            {**child, "page_level": 1, "parent_page_id": None},
            target,
        ],
        "page_hashes": before["page_hashes"],
    }
    subtree_after = {
        "items": [section, {**target, "order": 0}, {**parent, "order": 1}, {**child, "order": 2}],
        "page_hashes": before["page_hashes"],
    }
    after = {
        "items": [section, changed_parent, changed, changed_child],
        "page_hashes": before["page_hashes"],
    }
    sorted_after = {
        "items": [
            section,
            changed_parent,
            {**changed_child, "order": 1},
            {**changed, "order": 2},
        ],
        "page_hashes": before["page_hashes"],
    }
    snapshots = iter([
        before,
        root_only_after,
        root_before_child_restore,
        before,
        subtree_after,
        before,
        after,
        sorted_after,
        sorted_after,
        before,
    ])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    rejection_calls: list[dict] = []

    async def fake_expected_rejection(
        _client, tool_name, arguments, evidence_path, *, label, expected_message_fragment
    ):
        rejection_calls.append(arguments)
        evidence = {
            "tool": tool_name,
            "label": label,
            "expected_message_fragment": expected_message_fragment,
            "mutation_attempted": False,
        }
        test_utils.write_json(evidence_path, evidence)
        return evidence

    FakeClient.calls = []
    FakeClient.last_response = None
    FakeClient.response_item = changed
    monkeypatch.setattr(reorder_page_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(reorder_page_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(
        reorder_page_scenario,
        "expect_mutation_preflight_rejection",
        fake_expected_rejection,
    )
    monkeypatch.setattr(reorder_page_scenario, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        ReorderPageScenario().execute(
            SimpleNamespace(notebook_name=None, page_level=2, keep_worksite=False),
            RuntimeOptions(tmp_path, 10, False, False),
            {
                "schema_version": 1,
                "notebook": {"id": "notebook-id", "name": "Notebook"},
                "structure": {
                    "reorder_section": section,
                    "parent_page": parent,
                    "child_page": child,
                    "sibling_page": target,
                },
            },
            client=None,
            fixture_result={},
        )
    )

    assert [name for name, _ in FakeClient.calls] == [
        "reorder_page",
        "reorder_page",
        "reorder_page",
        "reorder_page",
        "reorder_page",
        "reorder_page",
        "sort_children",
        "reorder_page",
    ]
    assert rejection_calls[0]["child_type"] == "section"
    assert result["expected_rejection"]["mutation_attempted"] is False
    assert result["restored"] is True
    assert (
        tmp_path / "scenarios" / "reorder-page" / "expected-sort-rejection.json"
    ).exists()


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
    response_targets = iter(["section-1", "section-2", "section-3"])

    async def fake_snapshot(_client, _notebook_id):
        snapshot = next(snapshots)
        if _client.last_response is not None:
            target_id = next(response_targets)
            _client.last_response["destination_position"] = expected_destination_position(
                snapshot,
                target_id,
            )
        return snapshot

    FakeClient.calls = []
    FakeClient.last_response = None
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
    forward_targets = iter(["section-1", "section-2", "section-3"])

    async def fake_snapshot(_client, _notebook_id):
        snapshot_value = next(snapshots)
        if _client.last_response is not None:
            try:
                target_id = next(forward_targets)
            except StopIteration:
                pass
            else:
                _client.last_response["destination_position"] = expected_destination_position(
                    snapshot_value,
                    target_id,
                )
        return snapshot_value

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
    FakeClient.last_response = None
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
    assert [call["items"][0]["section_id"] for call in reparent_calls] == [
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
    source_notebook = {
        "resource_type": "notebook",
        "id": "source-notebook",
        "name": "Source Notebook",
    }
    destination_notebook = {
        "resource_type": "notebook",
        "id": "destination-notebook",
        "name": "Destination Notebook",
    }
    source_section = {
        "resource_type": "section",
        "id": "source-section",
        "name": "Source",
        "parent_id": "source-notebook",
    }
    destination = {
        "resource_type": "section",
        "id": "destination-section",
        "name": "Destination",
        "parent_id": "destination-notebook",
    }
    destination_anchor_a = {
        "resource_type": "page",
        "id": "destination-anchor-a",
        "title": "00-Destination-Anchor-A",
        "section_id": "destination-section",
        "parent_id": "destination-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
        "modified": "before",
    }
    destination_root_title_anchor = {
        **destination_anchor_a,
        "id": "destination-root-title",
        "title": SPECIAL_PAGE_TITLE,
        "order": 1,
    }
    destination_subtree_title_anchor = {
        **destination_anchor_a,
        "id": "destination-subtree-title",
        "title": "03-Subtree",
        "order": 2,
    }
    destination_anchor_b = {
        **destination_anchor_a,
        "id": "destination-anchor-b",
        "title": "99-Destination-Anchor-B",
        "order": 3,
    }
    root_only = {
        "resource_type": "page",
        "id": "root-only",
        "title": "01-Root-Only",
        "section_id": "source-section",
        "parent_id": "source-section",
        "parent_page_id": None,
        "page_level": 1,
        "order": 0,
        "modified": "before",
    }
    root_child = {
        **root_only,
        "id": "root-child",
        "title": "02-Root-Only-Child",
        "parent_page_id": "root-only",
        "page_level": 2,
        "order": 1,
    }
    subtree = {
        **root_only,
        "id": "subtree",
        "title": "03-Subtree",
        "parent_page_id": None,
        "page_level": 1,
        "order": 2,
    }
    subtree_child = {
        **root_child,
        "id": "subtree-child",
        "title": "04-Subtree-Child",
        "parent_page_id": "subtree",
        "order": 3,
    }
    state = {
        "source": [source_notebook, source_section, root_only, root_child, subtree, subtree_child],
        "destination": [
            destination_notebook,
            destination,
            destination_anchor_a,
            destination_root_title_anchor,
            destination_subtree_title_anchor,
            destination_anchor_b,
        ],
        "hashes": {
            "root-only": "root-hash",
            "root-child": "root-child-hash",
            "subtree": "subtree-hash",
            "subtree-child": "subtree-child-hash",
            "destination-anchor-a": "destination-anchor-a-hash",
            "destination-root-title": "destination-root-title-hash",
            "destination-subtree-title": "destination-subtree-title-hash",
            "destination-anchor-b": "destination-anchor-b-hash",
        },
    }

    async def fake_snapshot(_client, notebook_id, **_kwargs):
        role = "source" if notebook_id == "source-notebook" else "destination"
        ids = {item["id"] for item in state[role] if item.get("resource_type") == "page"}
        return {
            "notebook_id": notebook_id,
            "items": [dict(item) for item in state[role]],
            "page_hashes": {key: value for key, value in state["hashes"].items() if key in ids},
            "page_datetime_seconds": {
                page_id: "2026-01-01T00:00:00Z" for page_id in ids
            },
        }

    class FakeMovePageClient:
        policy = move_page_scenario.MOVE_PAGE_POLICY
        allowed_tools = move_page_scenario.MOVE_PAGE_TOOLS | {
            "health_check"
        }
        timeout_seconds = 1_800

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.arguments: list[dict] = []

        async def call_tool(self, name: str, arguments: dict) -> dict:
            self.calls.append(name)
            self.arguments.append(dict(arguments))
            assert name == "move_page"
            include_descendants = bool(arguments.get("include_subpages", False))
            if include_descendants:
                source_ids = ["subtree", "subtree-child"]
                target_ids = ["target-subtree", "target-subtree-child"]
            else:
                source_ids = ["root-only"]
                target_ids = ["target-root"]
                child = next(item for item in state["source"] if item["id"] == "root-child")
                child.update(page_level=1, parent_page_id=None, order=0)
            state["source"] = [item for item in state["source"] if item["id"] not in source_ids]
            for index, (source_id, target_id) in enumerate(zip(source_ids, target_ids)):
                original = root_only if source_id == "root-only" else (
                    subtree if source_id == "subtree" else subtree_child
                )
                state["destination"].append(
                    {
                        **original,
                        "id": target_id,
                        "title": (
                            arguments.get("destination_title", original["title"])
                            if index == 0
                            else original["title"]
                        ),
                        "section_id": "destination-section",
                        "parent_id": "destination-section",
                        "parent_page_id": target_ids[0] if index else None,
                        "page_level": index + 1,
                        "order": index,
                    }
                )
                state["hashes"][target_id] = state["hashes"][source_id]
            response = {
                "item": next(item for item in state["destination"] if item["id"] == target_ids[0]),
                "created_ids": target_ids,
                "copy_report": {
                    "verified": True,
                    "lossless": True,
                    "copy_contract_satisfied": True,
                    "id_map": dict(zip(source_ids, target_ids)),
                    "allocated_ids": list(target_ids),
                    "page_results": [
                        {
                            "source_page_id": source_id,
                            "target_page_id": target_ids[source_ids.index(source_id)],
                            "date_time": {"status": "verified"},
                            "lossless": True,
                            "equivalence": {
                                "verification_tier": "semantic_content_v1",
                                "equivalent": True,
                                "failed_content_object_types": [],
                                "semantic_content_comparison": {
                                    "source_complete": True,
                                    "target_complete": True,
                                    "passed": True,
                                },
                            },
                            "semantic_content_stages": {
                                "title_override_requested": (
                                    source_id == source_ids[0]
                                    and "destination_title" in arguments
                                    and arguments["destination_title"]
                                    != original["title"]
                                ),
                                "source_to_transformed": {
                                    "passed": True,
                                    "checks": {"title": True},
                                },
                                "transformed_to_target": {
                                    "passed": True,
                                    "checks": {"title": True},
                                },
                            },
                            "title_readback_stages": {
                                "title_override_requested": (
                                    source_id == source_ids[0]
                                    and "destination_title" in arguments
                                    and arguments["destination_title"]
                                    != original["title"]
                                ),
                                "source_to_transformed": {
                                    "passed": True,
                                    "checks": {"title": True},
                                },
                                "transformed_to_target": {
                                    "passed": True,
                                    "checks": {"title": True},
                                },
                            },
                        }
                        for source_id in source_ids
                    ],
                },
                "include_descendants": include_descendants,
                "source_deleted_nonpermanently": True,
                "recycle_bin_verification": "not_required_com_unavailable",
                "recycled_source_ids": [],
                "recycle_unverified_source_ids": list(reversed(source_ids)),
                "attempted_source_ids": list(reversed(source_ids)),
                "deleted_source_ids": list(reversed(source_ids)),
                "preserved_descendants": {
                    "promoted": not include_descendants,
                    "preserved_descendant_ids": [] if include_descendants else ["root-child"],
                },
            }
            response["destination_position"] = expected_destination_position(
                {"items": [*state["source"], *state["destination"]]},
                target_ids[0],
            )
            return response

    manifest = {
        "schema_version": 1,
        "notebook": source_notebook,
        "notebooks": {
            "source": source_notebook,
            "destination": destination_notebook,
        },
        "structure": {
            "source_section": source_section,
            "root_only_page": root_only,
            "root_only_child": root_child,
            "subtree_page": subtree,
            "subtree_child": subtree_child,
            "destination_section": destination,
            "destination_anchor_a": destination_anchor_a,
            "destination_root_title_anchor": destination_root_title_anchor,
            "destination_subtree_title_anchor": destination_subtree_title_anchor,
            "destination_anchor_b": destination_anchor_b,
        },
    }
    client = FakeMovePageClient()
    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.move_page_snapshot.capture_snapshot",
        fake_snapshot,
    )
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

    assert client.calls == ["move_page", "move_page"]
    assert "destination_title" not in client.arguments[0]
    assert client.arguments[0]["expected_title"] == root_only["title"]
    assert client.arguments[1]["destination_title"] == "03-Subtree"
    assert result["status"] == "passed"
    assert result["source_deleted_nonpermanently"] is True
    assert [case["effective_include_descendants"] for case in result["case_results"]] == [
        False,
        True,
    ]
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "move-page" / "worksite.json"
    )
    assert worksite["target_ids"] == [
        "target-root",
        "target-subtree",
        "target-subtree-child",
    ]
