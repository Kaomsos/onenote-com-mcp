"""Pure restore and keep-worksite contracts for container reorder scenarios."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.manual_validation import test_utils
from tests.manual_validation.mcp_stdio_client import REORDER_SECTION_GROUP_POLICY
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios.common import container_reorder
from tests.manual_validation.scenarios.common.config import REORDER_SECTION_GROUP_TOOLS


def snapshots():
    notebook = {"resource_type": "notebook", "id": "n", "name": "Notebook", "parent_id": None}
    parent = {
        "resource_type": "section_group",
        "id": "parent",
        "name": "Parent",
        "parent_id": "n",
    }
    root_groups = [
        {"resource_type": "section_group", "id": f"g{letter}", "name": f"Group {letter}", "parent_id": "n"}
        for letter in "ABC"
    ]
    nested_groups = [
        {
            "resource_type": "section_group",
            "id": f"nested{letter}",
            "name": f"Nested {letter}",
            "parent_id": "parent",
        }
        for letter in "ABC"
    ]

    def descendants(prefix, group_id, letter):
        section_id = f"{prefix}s{letter}"
        return [
            {
                "resource_type": "section",
                "id": section_id,
                "name": f"{prefix} Section {letter}",
                "parent_id": group_id,
            },
            {
                "resource_type": "page",
                "id": f"{prefix}p{letter}",
                "title": f"{prefix} Page {letter}",
                "parent_id": section_id,
                "section_id": section_id,
                "order": 0,
                "page_level": 1,
                "parent_page_id": None,
            },
        ]

    def build(root_order, nested_order):
        items = [notebook, parent]
        for index in nested_order:
            group = nested_groups[index]
            items.extend([group, *descendants("nested", group["id"], "ABC"[index])])
        for index in root_order:
            group = root_groups[index]
            items.extend([group, *descendants("root", group["id"], "ABC"[index])])
        page_ids = [f"{prefix}p{letter}" for prefix in ("root", "nested") for letter in "ABC"]
        return {
            "notebook_id": "n",
            "items": items,
            "page_hashes": {page_id: "same" for page_id in page_ids},
            "page_objects": {page_id: [] for page_id in page_ids},
        }

    return (
        build([0, 1, 2], [0, 1, 2]),
        build([0, 2, 1], [0, 1, 2]),
        build([0, 2, 1], [0, 2, 1]),
        build([0, 2, 1], [0, 1, 2]),
        build([0, 1, 2], [0, 1, 2]),
    )


class FakeClient:
    calls = []

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def call_tool(self, name, arguments, **_):
        self.calls.append((name, arguments))
        return {"ok": True, "complete": True}


def manifest():
    return {
        "schema_version": 1,
        "notebook": {"id": "n", "name": "Notebook"},
        "structure": {
            "root_group_a": {"id": "gA"},
            "root_group_c": {"id": "gC"},
            "nested_group_a": {"id": "nestedA"},
            "nested_group_c": {"id": "nestedC"},
        },
    }


def test_section_group_reorder_restores_exact_original_predecessor(monkeypatch, tmp_path):
    values = iter(snapshots())

    async def capture(_client, _notebook_id):
        return next(values)

    FakeClient.calls = []
    monkeypatch.setattr(container_reorder, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(container_reorder, "capture_snapshot", capture)
    monkeypatch.setattr(container_reorder, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        container_reorder.execute_container_reorder(
            args=SimpleNamespace(notebook_name=None, keep_worksite=False),
            options=RuntimeOptions(tmp_path, 10, False, False),
            manifest=manifest(),
            scenario_name="reorder-section-group",
            resource_type="section_group",
            tool_name="reorder_section_group",
            id_parameter="section_group_id",
            after_parameter="after_section_group_id",
            plans=(
                ("root_group_c", "root_group_a"),
                ("nested_group_c", "nested_group_a"),
            ),
            policy=REORDER_SECTION_GROUP_POLICY,
            allowed_tools=REORDER_SECTION_GROUP_TOOLS,
            client=None,
        )
    )

    assert result["restored"] is True
    assert [call[1]["after_section_group_id"] for call in FakeClient.calls] == [
        "gA",
        "nestedA",
        "nestedB",
        "gB",
    ]
    assert (tmp_path / "scenarios" / "reorder-section-group" / "restored.json").exists()


def test_section_group_reorder_keep_worksite_skips_restore(monkeypatch, tmp_path):
    before, forward_root, after, *_ = snapshots()
    values = iter([before, forward_root, after])

    async def capture(_client, _notebook_id):
        return next(values)

    FakeClient.calls = []
    monkeypatch.setattr(container_reorder, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(container_reorder, "capture_snapshot", capture)
    monkeypatch.setattr(container_reorder, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        container_reorder.execute_container_reorder(
            args=SimpleNamespace(notebook_name=None, keep_worksite=True),
            options=RuntimeOptions(tmp_path, 10, False, False),
            manifest=manifest(),
            scenario_name="reorder-section-group",
            resource_type="section_group",
            tool_name="reorder_section_group",
            id_parameter="section_group_id",
            after_parameter="after_section_group_id",
            plans=(
                ("root_group_c", "root_group_a"),
                ("nested_group_c", "nested_group_a"),
            ),
            policy=REORDER_SECTION_GROUP_POLICY,
            allowed_tools=REORDER_SECTION_GROUP_TOOLS,
            client=None,
        )
    )

    assert result["worksite_preserved"] is True
    assert len(FakeClient.calls) == 2
    worksite = test_utils.read_json(
        tmp_path / "scenarios" / "reorder-section-group" / "worksite.json"
    )
    assert worksite["operations"][0]["original_after_id"] == "gB"
    assert worksite["operations"][0]["temporary_after_id"] == "gA"
    assert worksite["operations"][1]["original_after_id"] == "nestedB"
    assert worksite["operations"][1]["temporary_after_id"] == "nestedA"
