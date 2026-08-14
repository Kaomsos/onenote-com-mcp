"""Pure contracts for same-Notebook typed Page and SectionGroup reparent scenarios."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.manual_validation.mcp_stdio_client import REPARENT_POLICY
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios.common import reparent as reparent_runtime
from tests.manual_validation.scenarios.common.destination_position import (
    expected_destination_position,
)
from tests.manual_validation.scenarios.common.config import (
    REPARENT_PAGE_TOOLS,
    REPARENT_SECTION_GROUP_TOOLS,
)
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult, FixtureValidationContext
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.specs import SCENARIO_SPECS
from tests.manual_validation.scenarios.fixture_recipes.reparent_page import DESCRIPTION as REPARENT_PAGE_DESCRIPTION
from tests.manual_validation.scenarios.fixture_recipes.reparent_section_group import DESCRIPTION as REPARENT_SECTION_GROUP_DESCRIPTION
from tests.manual_validation.scenarios.reparent_page import ReparentPageScenario
from tests.manual_validation.scenarios import reparent_page_scope as reparent_scope_runtime
from tests.manual_validation.scenarios.reparent_page_scope import ReparentPageScopeScenario
from tests.manual_validation.scenarios.reparent_section_group import (
    ReparentSectionGroupScenario,
)


def _validate_fixture_snapshot(name, snapshot, structure, content_fixture):
    evidence = {}
    if content_fixture is not None:
        evidence["reparent_page_fixture"] = content_fixture
    return list(
        SCENARIO_REGISTRY.get(name).fixture_recipe.validate(
            FixtureValidationContext(
                args=SimpleNamespace(scenario=name), snapshot=snapshot
            ),
            FixtureBuildResult(structure, evidence),
        )
    )


def test_reparent_page_recipe_keeps_validated_v3_cache_identity() -> None:
    recipe = SCENARIO_REGISTRY.get("reparent-page").fixture_recipe
    assert recipe.recipe_version == 3


class FakeClient:
    def __init__(self, allowed_tools: set[str], timeout_seconds: int = 180) -> None:
        self.allowed_tools = set(allowed_tools) | {"get_page_text"}
        self.policy = REPARENT_POLICY
        self.timeout_seconds = timeout_seconds
        self.calls: list[tuple[str, dict]] = []
        self.last_response: dict | None = None

    async def call_tool(self, name: str, arguments: dict, **_kwargs) -> dict:
        self.calls.append((name, arguments))
        target_id = arguments.get("page_id") or arguments.get("section_group_id")
        current_id = {
            "target-page": "reparented-page",
            "reparented-page": "restored-page",
        }.get(target_id, target_id)
        self.last_response = {
            "ok": True,
            "complete": True,
            "id_map": {target_id: current_id},
        }
        return self.last_response


def _snapshot(items: list[dict]) -> dict:
    pages = [item for item in items if item["resource_type"] == "page"]
    return {
        "notebook_id": "notebook",
        "items": deepcopy(items),
        "page_hashes": {item["id"]: f"hash-{item['id']}" for item in pages},
        "page_canonical_hashes": {
            item["id"]: f"canonical-{item['id']}" for item in pages
        },
        "page_reparent_hashes": {
            item["id"]: f"reparent-{item['id']}" for item in pages
        },
        "page_objects": {
            item["id"]: [
                {"object_id": f"object-{item['id']}", "kind": "Outline"}
            ]
            for item in pages
        },
    }


def _with_parent(snapshot: dict, target_id: str, parent_id: str, *, page: bool = False) -> dict:
    changed = deepcopy(snapshot)
    target = next(item for item in changed["items"] if item["id"] == target_id)
    target["parent_id"] = parent_id
    if page:
        target["section_id"] = parent_id
    return changed


def _remap_page(snapshot: dict, old_id: str, new_id: str, parent_id: str) -> dict:
    changed = deepcopy(snapshot)
    target = next(item for item in changed["items"] if item["id"] == old_id)
    target["id"] = new_id
    target["parent_id"] = parent_id
    target["section_id"] = parent_id
    changed["page_hashes"][new_id] = f"hash-{new_id}"
    del changed["page_hashes"][old_id]
    changed["page_canonical_hashes"][new_id] = changed["page_canonical_hashes"].pop(
        old_id
    )
    changed["page_reparent_hashes"][new_id] = changed["page_reparent_hashes"].pop(
        old_id
    )
    changed["page_objects"][new_id] = [
        {"object_id": f"object-{new_id}", "kind": "Outline"}
    ]
    del changed["page_objects"][old_id]
    return changed


def _page_case() -> tuple[dict, dict, list[dict], list[dict], type, set[str]]:
    items = [
        {"resource_type": "notebook", "id": "notebook", "name": "Notebook", "parent_id": None},
        {
            "resource_type": "section",
            "id": "description-section",
            "name": "00-Description",
            "parent_id": "notebook",
            "notebook_id": "notebook",
        },
        {
            "resource_type": "page",
            "id": "description-page",
            "title": "00-Reparent-Page-Description",
            "parent_id": "description-section",
            "notebook_id": "notebook",
            "section_id": "description-section",
            "page_level": 1,
            "order": 0,
            "parent_page_id": None,
        },
        {
            "resource_type": "section",
            "id": "source-section",
            "name": "01-Source-Section",
            "parent_id": "notebook",
            "notebook_id": "notebook",
        },
        {
            "resource_type": "section",
            "id": "destination-section",
            "name": "02-Destination-Section",
            "parent_id": "notebook",
            "notebook_id": "notebook",
        },
        {
            "resource_type": "page",
            "id": "target-page",
            "title": "01-Reparent-Page",
            "parent_id": "source-section",
            "notebook_id": "notebook",
            "section_id": "source-section",
            "page_level": 1,
            "order": 0,
            "parent_page_id": None,
        },
        {
            "resource_type": "page",
            "id": "anchor-page",
            "title": "02-Destination-Anchor",
            "parent_id": "destination-section",
            "notebook_id": "notebook",
            "section_id": "destination-section",
            "page_level": 1,
            "order": 0,
            "parent_page_id": None,
        },
        {
            "resource_type": "page",
            "id": "anchor-page-b",
            "title": "03-Destination-Anchor",
            "parent_id": "destination-section",
            "notebook_id": "notebook",
            "section_id": "destination-section",
            "page_level": 1,
            "order": 1,
            "parent_page_id": None,
        },
    ]
    before = _snapshot(items)
    after = _remap_page(before, "target-page", "reparented-page", "destination-section")
    restored = _remap_page(after, "reparented-page", "restored-page", "source-section")
    manifest = {
        "schema_version": 1,
        "notebook": items[0],
        "structure": {
            "description_section": items[1],
            "description_page": items[2],
            "source_section": items[3],
            "destination_section": items[4],
            "reparent_page": items[5],
            "destination_anchor_page": items[6],
            "destination_anchor_page_b": items[7],
        },
    }
    return manifest, before, [after], [restored], ReparentPageScenario, REPARENT_PAGE_TOOLS


def _section_group_case() -> tuple[dict, dict, list[dict], list[dict], type, set[str]]:
    notebook = {
        "resource_type": "notebook",
        "id": "notebook",
        "name": "Notebook",
        "parent_id": None,
    }
    items = [
        notebook,
        {
            "resource_type": "section",
            "id": "description-section",
            "name": "00-Description",
            "parent_id": "notebook",
            "notebook_id": "notebook",
        },
        {
            "resource_type": "page",
            "id": "description-page",
            "title": "00-Reparent-SectionGroup-Description",
            "parent_id": "description-section",
            "notebook_id": "notebook",
            "section_id": "description-section",
            "page_level": 1,
            "order": 0,
            "parent_page_id": None,
        },
    ]

    def group(object_id: str, name: str, parent_id: str) -> dict:
        return {
            "resource_type": "section_group",
            "id": object_id,
            "name": name,
            "parent_id": parent_id,
            "notebook_id": "notebook",
        }

    cases = [
        (
            group("destination-1", "01-Destination-Parent", "notebook"),
            group("target-1", "01-Notebook-To-Group-Target", "notebook"),
            "01-Descendant-Section",
            "01-Descendant-Page",
        ),
        (
            group("source-2", "02-Source-Parent", "notebook"),
            group("target-2", "02-Group-To-Notebook-Target", "source-2"),
            "02-Descendant-Section",
            "02-Descendant-Page",
        ),
        (
            group("source-3", "03-Source-Parent", "notebook"),
            group("target-3", "03-Group-To-Group-Target", "source-3"),
            "03-Descendant-Section",
            "03-Descendant-Page",
        ),
    ]
    destination_3 = group("destination-3", "03-Destination-Parent", "notebook")
    anchors = [
        group("anchor-1a", "00-Group-Anchor-A", "destination-1"),
        group("anchor-1b", "99-Group-Anchor-B", "destination-1"),
        group("anchor-2a", "00-Notebook-Group-Anchor-A", "notebook"),
        group("anchor-2b", "99-Notebook-Group-Anchor-B", "notebook"),
        group("anchor-3a", "00-Group-Anchor-A", "destination-3"),
        group("anchor-3b", "99-Group-Anchor-B", "destination-3"),
    ]
    for parent, target, section_name, page_name in cases:
        items.extend(
            [
                parent,
                target,
                {
                    "resource_type": "section",
                    "id": f"section-{target['id'][-1]}",
                    "name": section_name,
                    "parent_id": target["id"],
                    "notebook_id": "notebook",
                },
                {
                    "resource_type": "page",
                    "id": f"page-{target['id'][-1]}",
                    "title": page_name,
                    "parent_id": f"section-{target['id'][-1]}",
                    "notebook_id": "notebook",
                    "section_id": f"section-{target['id'][-1]}",
                    "page_level": 1,
                    "order": 0,
                    "parent_page_id": None,
                },
            ]
        )
    items.extend([destination_3, *anchors])
    before = _snapshot(items)
    after_1 = _with_parent(before, "target-1", "destination-1")
    after_2 = _with_parent(after_1, "target-2", "notebook")
    after_3 = _with_parent(after_2, "target-3", "destination-3")
    restore_1 = _with_parent(after_3, "target-3", "source-3")
    restore_2 = _with_parent(restore_1, "target-2", "source-2")
    restore_3 = _with_parent(restore_2, "target-1", "notebook")

    by_id = {item["id"]: item for item in items}
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "description_section": by_id["description-section"],
            "description_page": by_id["description-page"],
            "notebook_to_group_destination": by_id["destination-1"],
            "notebook_to_group_anchor_a": by_id["anchor-1a"],
            "notebook_to_group_anchor_b": by_id["anchor-1b"],
            "notebook_to_group_target": by_id["target-1"],
            "notebook_to_group_section": by_id["section-1"],
            "notebook_to_group_page": by_id["page-1"],
            "group_to_notebook_source": by_id["source-2"],
            "group_to_notebook_target": by_id["target-2"],
            "group_to_notebook_section": by_id["section-2"],
            "group_to_notebook_page": by_id["page-2"],
            "group_to_notebook_anchor_a": by_id["anchor-2a"],
            "group_to_notebook_anchor_b": by_id["anchor-2b"],
            "group_to_group_source": by_id["source-3"],
            "group_to_group_destination": by_id["destination-3"],
            "group_to_group_anchor_a": by_id["anchor-3a"],
            "group_to_group_anchor_b": by_id["anchor-3b"],
            "group_to_group_target": by_id["target-3"],
            "group_to_group_section": by_id["section-3"],
            "group_to_group_page": by_id["page-3"],
        },
    }
    return (
        manifest,
        before,
        [after_1, after_2, after_3],
        [restore_1, restore_2, restore_3],
        ReparentSectionGroupScenario,
        REPARENT_SECTION_GROUP_TOOLS,
    )


def _page_scope_case() -> tuple[dict, dict, dict, dict]:
    notebook = {"resource_type": "notebook", "id": "notebook", "name": "Notebook", "parent_id": None}
    source = {"resource_type": "section", "id": "source", "name": "Source", "parent_id": "notebook", "notebook_id": "notebook"}
    destination = {"resource_type": "section", "id": "destination", "name": "Destination", "parent_id": "notebook", "notebook_id": "notebook"}

    def page(object_id: str, title: str, level: int, order: int, parent: str | None) -> dict:
        return {
            "resource_type": "page",
            "id": object_id,
            "title": title,
            "parent_id": "source",
            "notebook_id": "notebook",
            "section_id": "source",
            "page_level": level,
            "order": order,
            "parent_page_id": parent,
        }

    pages = [
        page("root-parent", "Root Parent", 1, 0, None),
        page("root-selected", "Root Selected", 2, 1, "root-parent"),
        page("root-child", "Root Child", 3, 2, "root-selected"),
        page("root-grandchild", "Root Grandchild", 4, 3, "root-child"),
        page("tree-parent", "Tree Parent", 1, 4, None),
        page("tree-selected", "Tree Selected", 2, 5, "tree-parent"),
        page("tree-child-a", "Tree Child A", 3, 6, "tree-selected"),
        page("tree-grandchild", "Tree Grandchild", 4, 7, "tree-child-a"),
        page("tree-child-b", "Tree Child B", 3, 8, "tree-selected"),
    ]
    anchors = [
        {**page("anchor-a", "Anchor A", 1, 0, None), "parent_id": "destination", "section_id": "destination"},
        {**page("anchor-b", "Anchor B", 1, 1, None), "parent_id": "destination", "section_id": "destination"},
    ]
    before = _snapshot([notebook, source, destination, *pages, *anchors])
    after_root = deepcopy(before)
    after_root["items"] = [item for item in after_root["items"] if item["id"] != "root-selected"]
    for item in after_root["items"]:
        if item["id"] == "root-child":
            item.update(page_level=2, parent_page_id="root-parent")
        elif item["id"] == "root-grandchild":
            item.update(page_level=3, parent_page_id="root-child")
    root_new = {
        **next(item for item in before["items"] if item["id"] == "root-selected"),
        "id": "root-new",
        "parent_id": "destination",
        "section_id": "destination",
        "page_level": 1,
        "order": 2,
        "parent_page_id": None,
    }
    after_root["items"].append(root_new)
    for field in ("page_hashes", "page_canonical_hashes", "page_reparent_hashes", "page_objects"):
        value = after_root[field].pop("root-selected")
        after_root[field]["root-new"] = value

    selected_ids = ["tree-selected", "tree-child-a", "tree-grandchild", "tree-child-b"]
    after_tree = deepcopy(after_root)
    after_tree["items"] = [item for item in after_tree["items"] if item["id"] not in selected_ids]
    id_map = {source_id: f"new-{source_id}" for source_id in selected_ids}
    levels = [1, 2, 3, 2]
    parents = [None, id_map["tree-selected"], id_map["tree-child-a"], id_map["tree-selected"]]
    for order, (source_id, level, parent_id) in enumerate(zip(selected_ids, levels, parents), start=3):
        old = next(item for item in before["items"] if item["id"] == source_id)
        after_tree["items"].append(
            {
                **old,
                "id": id_map[source_id],
                "parent_id": "destination",
                "section_id": "destination",
                "page_level": level,
                "order": order,
                "parent_page_id": parent_id,
            }
        )
        for field in ("page_hashes", "page_canonical_hashes", "page_reparent_hashes", "page_objects"):
            value = after_tree[field].pop(source_id)
            after_tree[field][id_map[source_id]] = value
    structure = {item["id"].replace("-", "_"): item for item in [source, destination, *pages, *anchors]}
    structure.update(
        {
            "source_section": source,
            "destination_section": destination,
            "root_only_selected": next(item for item in pages if item["id"] == "root-selected"),
            "root_only_child": next(item for item in pages if item["id"] == "root-child"),
            "root_only_grandchild": next(item for item in pages if item["id"] == "root-grandchild"),
            "subtree_selected": next(item for item in pages if item["id"] == "tree-selected"),
            "subtree_child_a": next(item for item in pages if item["id"] == "tree-child-a"),
            "subtree_grandchild": next(item for item in pages if item["id"] == "tree-grandchild"),
            "subtree_child_b": next(item for item in pages if item["id"] == "tree-child-b"),
        }
    )
    manifest = {"schema_version": 1, "notebook": notebook, "structure": structure}
    return manifest, before, after_root, after_tree


@pytest.mark.parametrize("case", [_page_case, _section_group_case])
@pytest.mark.parametrize("keep_worksite", [False, True])
def test_typed_reparent_verifies_identity_content_and_restore_or_preserve(
    monkeypatch, tmp_path, case, keep_worksite
) -> None:
    manifest, before, forwards, restores, scenario_type, allowed_tools = case()
    snapshots = iter([before, *forwards] if keep_worksite else [before, *forwards, *restores])

    async def fake_snapshot(_client, _notebook_id):
        snapshot = deepcopy(next(snapshots))
        if client.last_response is not None:
            current_id = next(iter(client.last_response["id_map"].values()))
            client.last_response["destination_position"] = expected_destination_position(
                snapshot,
                str(current_id),
            )
        return snapshot

    monkeypatch.setattr(reparent_runtime, "capture_snapshot", fake_snapshot)
    client = FakeClient(allowed_tools)
    result = asyncio.run(
        scenario_type().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=keep_worksite),
            RuntimeOptions(tmp_path, 180, False, False),
            manifest,
            client=client,
            fixture_result={},
        )
    )

    operation_count = len(forwards)
    assert result["status"] == "passed"
    assert result["restored"] is (not keep_worksite)
    assert result["worksite_preserved"] is keep_worksite
    assert all(all(checks.values()) for checks in result["verified"].values())
    expected_tool = (
        "reparent_page" if scenario_type is ReparentPageScenario else "reparent_section_group"
    )
    assert [name for name, _arguments in client.calls] == [expected_tool] * (
        operation_count if keep_worksite else operation_count * 2
    )

    if scenario_type is ReparentPageScenario:
        assert client.calls[0][1] == {
            "page_id": "target-page",
            "destination_section_id": "destination-section",
            "expected_title": "01-Reparent-Page",
            "expected_section_id": "source-section",
            "expected_modified": None,
        }
        assert result["operations"][0]["id_history"] == (
            ["target-page", "reparented-page"]
            if keep_worksite
            else ["target-page", "reparented-page", "restored-page"]
        )
        assert result["operations"][0]["forward_id_map"] == {
            "target-page": "reparented-page"
        }
        if not keep_worksite:
            assert client.calls[1][1]["page_id"] == "reparented-page"
            assert result["operations"][0]["restore_id_maps"] == [
                {"reparented-page": "restored-page"}
            ]
    else:
        assert [arguments["section_group_id"] for _name, arguments in client.calls[:3]] == [
            "target-1",
            "target-2",
            "target-3",
        ]
        assert [arguments["destination_parent_id"] for _name, arguments in client.calls[:3]] == [
            "destination-1",
            "notebook",
            "destination-3",
        ]
        if not keep_worksite:
            restore_target_ids = [
                call[1]["section_group_id"] for call in client.calls[operation_count:]
            ]
            assert restore_target_ids == ["target-3", "target-2", "target-1"]


def test_typed_reparent_rejects_com_success_without_parent_change(monkeypatch, tmp_path) -> None:
    manifest, before, _forwards, _restores, scenario_type, allowed_tools = _page_case()
    snapshots = iter([before, before])

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(reparent_runtime, "capture_snapshot", fake_snapshot)
    client = FakeClient(allowed_tools)
    with pytest.raises(InvariantFailure, match="without applying the requested parent"):
        asyncio.run(
            scenario_type().execute(
                SimpleNamespace(notebook_name=None, keep_worksite=False),
                RuntimeOptions(tmp_path, 180, False, False),
                manifest,
                client=client,
                fixture_result={},
            )
        )
    assert [name for name, _arguments in client.calls] == ["reparent_page"]


def test_reparent_page_scope_runner_verifies_both_ranges_and_independent_positions(
    monkeypatch, tmp_path
) -> None:
    manifest, before, after_root, after_tree = _page_scope_case()
    snapshots = iter([before, after_root, after_tree])

    class ScopeClient:
        allowed_tools = set(REPARENT_PAGE_TOOLS)
        policy = REPARENT_POLICY
        timeout_seconds = 180

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.last_response: dict | None = None

        async def call_tool(self, name: str, arguments: dict) -> dict:
            assert name == "reparent_page"
            self.calls.append(dict(arguments))
            if len(self.calls) == 1:
                response = {
                    "include_descendants": False,
                    "id_map": {"root-selected": "root-new"},
                    "preserved_descendants": {
                        "promoted": True,
                        "preserved_descendant_ids": ["root-child", "root-grandchild"],
                    },
                }
            else:
                response = {
                    "include_descendants": True,
                    "id_map": {
                        source_id: f"new-{source_id}"
                        for source_id in (
                            "tree-selected",
                            "tree-child-a",
                            "tree-grandchild",
                            "tree-child-b",
                        )
                    },
                    "preserved_descendants": {
                        "promoted": False,
                        "preserved_descendant_ids": [],
                    },
                }
            self.last_response = response
            return response

    client = ScopeClient()

    async def fake_snapshot(_client, _notebook_id):
        snapshot = deepcopy(next(snapshots))
        if client.last_response is not None:
            target_id = next(iter(client.last_response["id_map"].values()))
            client.last_response["destination_position"] = expected_destination_position(
                snapshot, target_id
            )
        return snapshot

    monkeypatch.setattr(reparent_scope_runtime, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(reparent_scope_runtime, "render_report", lambda _path: None)

    result = asyncio.run(
        ReparentPageScopeScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=False),
            RuntimeOptions(tmp_path, 180, False, False),
            manifest,
            client=client,
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert [case["case"] for case in result["cases"]] == [
        "root-only-default",
        "full-subtree",
    ]
    assert "include_descendants" not in client.calls[0]
    assert client.calls[1]["include_descendants"] is True
    assert result["cases"][1]["target_parent_page_ids"] == {
        "new-tree-selected": None,
        "new-tree-child-a": "new-tree-selected",
        "new-tree-grandchild": "new-tree-child-a",
        "new-tree-child-b": "new-tree-selected",
    }
    scenario = tmp_path / "scenarios" / "reparent-page-scope"
    for case in ("root-only-default", "full-subtree"):
        assert (scenario / f"mutation-response-{case}.json").exists()
        assert (scenario / f"after-{case}.json").exists()
        assert (scenario / f"destination-position-evidence-{case}.json").exists()


def test_reparent_page_scope_runner_rejects_excluded_descendant_in_id_map(
    monkeypatch, tmp_path
) -> None:
    manifest, before, after_root, _after_tree = _page_scope_case()
    snapshots = iter([before, after_root])

    class BadClient:
        allowed_tools = set(REPARENT_PAGE_TOOLS)
        policy = REPARENT_POLICY
        timeout_seconds = 180
        response = {
            "include_descendants": False,
            "id_map": {
                "root-selected": "root-new",
                "root-child": "root-child",
            },
            "preserved_descendants": {
                "promoted": True,
                "preserved_descendant_ids": ["root-child", "root-grandchild"],
            },
        }

        async def call_tool(self, _name: str, _arguments: dict) -> dict:
            return self.response

    client = BadClient()

    async def fake_snapshot(_client, _notebook_id):
        snapshot = deepcopy(next(snapshots))
        client.response["destination_position"] = expected_destination_position(
            snapshot, "root-new"
        ) if any(item["id"] == "root-new" for item in snapshot["items"]) else {}
        return snapshot

    monkeypatch.setattr(reparent_scope_runtime, "capture_snapshot", fake_snapshot)

    with pytest.raises(InvariantFailure, match="incorrectly includes excluded"):
        asyncio.run(
            ReparentPageScopeScenario().execute(
                SimpleNamespace(notebook_name=None, keep_worksite=False),
                RuntimeOptions(tmp_path, 180, False, False),
                manifest,
                client=client,
                fixture_result={},
            )
        )


def test_reparent_page_rejects_ambiguous_identity_transition_without_restore(
    monkeypatch, tmp_path
) -> None:
    manifest, before, forwards, _restores, scenario_type, allowed_tools = _page_case()
    after = forwards[0]
    after["items"].append(
        {
            "resource_type": "page",
            "id": "unexpected-page",
            "title": "Unexpected",
            "parent_id": "destination-section",
            "notebook_id": "notebook",
            "section_id": "destination-section",
            "page_level": 1,
            "order": 2,
            "parent_page_id": None,
        }
    )
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return deepcopy(next(snapshots))

    monkeypatch.setattr(reparent_runtime, "capture_snapshot", fake_snapshot)
    client = FakeClient(allowed_tools)
    with pytest.raises(InvariantFailure, match="one exact old-ID to new-ID transition"):
        asyncio.run(
            scenario_type().execute(
                SimpleNamespace(notebook_name=None, keep_worksite=False),
                RuntimeOptions(tmp_path, 180, False, False),
                manifest,
                client=client,
                fixture_result={},
            )
        )
    assert [name for name, _arguments in client.calls] == ["reparent_page"]


def test_reparent_page_rejects_rich_content_change_after_valid_id_remap() -> None:
    _manifest, before, forwards, _restores, _scenario_type, _allowed_tools = _page_case()
    after = forwards[0]
    after["page_reparent_hashes"]["reparented-page"] = "changed-rich-content"

    with pytest.raises(InvariantFailure, match="changed rich content"):
        reparent_runtime._validate_page_reparented_snapshot(
            before,
            after,
            target_id="target-page",
            destination_parent_id="destination-section",
        )


def test_reparent_specs_use_typed_tools_without_raw_xml_and_fixtures_are_valid() -> None:
    for name, case in (
        ("reparent-page", _page_case),
        ("reparent-section-group", _section_group_case),
    ):
        manifest, before, _forwards, _restores, _scenario_type, _allowed_tools = case()
        spec = SCENARIO_SPECS[name]
        assert spec.policy == REPARENT_POLICY
        assert "update_hierarchy_xml" not in spec.tool_allowlist
        assert (
            "reparent_page" if name == "reparent-page" else "reparent_section_group"
        ) in spec.tool_allowlist
        assert "get_page_text" in spec.tool_allowlist
        assert not {
            "delete_page",
            "delete_section_group",
            "copy_page",
            "copy_section_group",
            "reparent_section",
        } & set(spec.tool_allowlist)
        content_fixture = None
        if name == "reparent-page":
            content_fixture = {
                "page_id": "target-page",
                "automated_content": ["rich_text", "table", "image", "list", "tag"],
                "list_tag": {
                    "page_id": "target-page",
                    "observed_capabilities": ["List", "Tag"],
                    "observed_counts": {"List": 3, "Tag": 3, "TagDef": 1},
                },
            }
        checks = _validate_fixture_snapshot(
            name, before, manifest["structure"], content_fixture
        )
        assert set(spec.fixture.validation_conditions) <= set(checks)


def test_reparent_descriptions_make_states_and_three_group_transitions_explicit() -> None:
    assert "操作前：01-Source-Section/01-Reparent-Page" in REPARENT_PAGE_DESCRIPTION
    assert "操作后：02-Destination-Section/01-Reparent-Page" in REPARENT_PAGE_DESCRIPTION
    assert "默认恢复后：01-Source-Section/01-Reparent-Page" in REPARENT_PAGE_DESCRIPTION
    assert "旧 ID → 新 ID" in REPARENT_PAGE_DESCRIPTION
    assert "Rich Text、Table、List、Tag 和 Image" in REPARENT_PAGE_DESCRIPTION
    assert "场景一：Notebook 父级 → SectionGroup 父级" in REPARENT_SECTION_GROUP_DESCRIPTION
    assert "场景二：SectionGroup 父级 → Notebook 父级" in REPARENT_SECTION_GROUP_DESCRIPTION
    assert "场景三：SectionGroup 父级 → SectionGroup 父级" in REPARENT_SECTION_GROUP_DESCRIPTION


def test_section_group_typed_call_accepts_notebook_destination() -> None:
    _manifest, before, _forwards, _restores, _scenario_type, _allowed_tools = _section_group_case()
    target = next(item for item in before["items"] if item["id"] == "target-2")
    name, arguments = reparent_runtime._typed_reparent_call(
        target, "notebook", "section_group"
    )
    assert name == "reparent_section_group"
    assert arguments["section_group_id"] == "target-2"
    assert arguments["destination_parent_id"] == "notebook"
