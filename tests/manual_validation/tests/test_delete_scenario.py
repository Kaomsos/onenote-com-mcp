"""Delete scenario manifest allowlist tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation import test_utils
from tests.manual_validation.scenarios import delete as delete_scenario
from tests.manual_validation.scenarios.delete import DeleteScenario


def test_delete_executes_with_its_minimal_group_only_manifest(monkeypatch, tmp_path) -> None:
    notebook = {"resource_type": "notebook", "id": "notebook-id", "name": "Notebook"}
    sandbox = {
        "resource_type": "section_group",
        "id": "sandbox-id",
        "name": "Delete-Sandbox",
        "parent_id": "notebook-id",
    }
    target = {
        "resource_type": "section_group",
        "id": "group-id",
        "name": "Disposable-Group",
        "parent_id": "sandbox-id",
    }
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {"delete_sandbox": sandbox, "disposable_group": target},
    }
    before = {
        "captured_at": "before",
        "notebook_id": "notebook-id",
        "items": [notebook, sandbox, target],
        "page_hashes": {},
        "page_objects": {},
    }
    after = {**before, "captured_at": "after", "items": [notebook, sandbox]}

    class FakeClient:
        calls: list[tuple[str, dict]] = []

        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            if name == "delete_section_group":
                return {"permanently": False}
            if name == "expand_hierarchy":
                return {
                    "tree": {
                        "item": notebook,
                        "children": [
                            {
                                "item": sandbox,
                                "children": [{"item": {**target, "is_in_recycle_bin": True}}],
                            }
                        ],
                    }
                }
            raise AssertionError(f"Unexpected tool call: {name}")

    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    monkeypatch.setattr(delete_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(delete_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(delete_scenario, "render_report", lambda _run_dir: None)
    args = SimpleNamespace(notebook_name=None)
    scenario = DeleteScenario()
    scenario.prepare_arguments(args, manifest)

    result = asyncio.run(
        scenario.execute(
            args,
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    assert result["target_key"] == "disposable_group"
    assert FakeClient.calls[0] == (
        "delete_section_group",
        {
            "section_group_id": "group-id",
            "expected_name": "Disposable-Group",
            "expected_parent_id": "sandbox-id",
            "expected_modified": None,
            "permanently": False,
        },
    )


def test_delete_runs_leaf_and_mixed_subpage_batches_independently(
    monkeypatch, tmp_path
) -> None:
    notebook = {"resource_type": "notebook", "id": "n", "name": "Notebook"}
    sandbox = {
        "resource_type": "section_group", "id": "sandbox", "name": "Delete-Sandbox",
        "parent_id": "n",
    }
    page_section = {
        "resource_type": "section", "id": "pages", "name": "Pages",
        "parent_id": "sandbox",
    }
    section_target = {
        "resource_type": "section", "id": "section-target", "name": "Section Target",
        "parent_id": "sandbox",
    }
    group_target = {
        "resource_type": "section_group", "id": "group-target", "name": "Group Target",
        "parent_id": "sandbox",
    }
    budget_section = {
        "resource_type": "section", "id": "budget", "name": "Budget",
        "parent_id": "sandbox",
    }

    def page(
        object_id: str,
        title: str,
        order: int,
        *,
        level: int = 1,
        parent_page_id: str | None = None,
        section_id: str = "pages",
    ) -> dict:
        return {
            "resource_type": "page", "id": object_id, "title": title,
            "name": title, "section_id": section_id, "parent_id": section_id,
            "order": order, "page_level": level,
            "parent_page_id": parent_page_id, "modified": f"m-{object_id}",
        }

    leaf1 = page("leaf-1", "Leaf 1", 4)
    leaf2 = page("leaf-2", "Leaf 2", 5)
    root_only = page("root-only", "Root Only", 0)
    protected = page(
        "protected", "Protected", 1, level=2, parent_page_id="root-only"
    )
    subtree = page("subtree", "Subtree", 2)
    subtree_child = page(
        "subtree-child", "Subtree Child", 3, level=2,
        parent_page_id="subtree",
    )
    budget_pages = [
        page(f"budget-{index}", f"Budget {index}", index, section_id="budget")
        for index in range(6)
    ]
    before = {
        "captured_at": "before",
        "notebook_id": "n",
        "items": [
            notebook, sandbox, page_section, section_target, group_target,
            budget_section, root_only, protected, subtree, subtree_child,
            leaf1, leaf2, *budget_pages,
        ],
        "page_hashes": {"protected": "protected-hash"},
        "page_objects": {},
    }
    protected_after = {
        **protected, "order": 0, "page_level": 1, "parent_page_id": None
    }
    after = {
        **before,
        "captured_at": "after",
        "items": [
            notebook, sandbox, page_section, budget_section, protected_after,
            *budget_pages,
        ],
    }
    manifest = {
        "schema_version": 1,
        "notebook": notebook,
        "structure": {
            "delete_sandbox": sandbox,
            "disposable_page_section": page_section,
            "disposable_page_leaf_target": leaf1,
            "disposable_page_leaf_target_second": leaf2,
            "disposable_page_target": root_only,
            "disposable_page_protected_child": protected,
            "disposable_page_target_second": subtree,
            "disposable_page_subtree_child": subtree_child,
            "disposable_section_target": section_target,
            "disposable_group": group_target,
            "budget_section": budget_section,
        },
    }

    class FakeClient:
        calls: list[tuple[str, dict]] = []

        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            if name == "delete_page":
                items = arguments["items"]
                selected_count = 3 if any(
                    item.get("include_subpages") for item in items
                ) else 2
                return {
                    "applied_count": len(items),
                    "items": [
                        {
                            "status": "applied",
                            "result": {"permanently": False},
                        }
                        for _item in items
                    ],
                    "final_hierarchy": {"item_count": selected_count},
                }
            if name in {"delete_section", "delete_section_group"}:
                return {
                    "applied_count": 1,
                    "items": [
                        {"status": "applied", "result": {"permanently": False}}
                    ],
                    "final_hierarchy": {"item_count": 1},
                }
            if name == "expand_hierarchy":
                recycled = [
                    {**value, "is_in_recycle_bin": True}
                    for value in (
                        leaf1, leaf2, root_only, subtree,
                        section_target, group_target,
                    )
                ]
                return {
                    "tree": {
                        "item": notebook,
                        "children": [
                            {"item": value, "children": []} for value in recycled
                        ],
                    }
                }
            raise AssertionError(name)

    snapshots = iter([before, before, after])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    async def fake_rejection(
        _client, _tool, _arguments, evidence_path, **_kwargs
    ):
        evidence = {"mutation_attempted": False, "budget_dimension": "effective_pages"}
        test_utils.write_json(evidence_path, evidence)
        return evidence

    FakeClient.calls = []
    monkeypatch.setattr(delete_scenario, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(delete_scenario, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(
        delete_scenario, "expect_mutation_preflight_rejection", fake_rejection
    )
    monkeypatch.setattr(delete_scenario, "render_report", lambda _run_dir: None)

    result = asyncio.run(
        DeleteScenario().execute(
            SimpleNamespace(notebook_name=None, keep_worksite=False),
            RuntimeOptions(tmp_path, 10, False, False),
            manifest,
            client=None,
            fixture_result={},
        )
    )

    page_calls = [arguments for name, arguments in FakeClient.calls if name == "delete_page"]
    assert len(page_calls) == 2
    assert [item["page_id"] for item in page_calls[0]["items"]] == [
        "leaf-1", "leaf-2"
    ]
    assert [item["include_subpages"] for item in page_calls[0]["items"]] == [
        False, False
    ]
    assert [item["page_id"] for item in page_calls[1]["items"]] == [
        "root-only", "subtree"
    ]
    assert [item["include_subpages"] for item in page_calls[1]["items"]] == [
        False, True
    ]
    assert result["large_notebook_small_page_batch"] == {
        "notebook_pages_exceed_effective_limit": True,
        "leaf_page_targets": 2,
        "applied": True,
    }
    assert result["include_subpages_validation"]["mixed_scope_batch_applied"] is True
    assert result["include_subpages_validation"]["protected_child_content_unchanged"] is True
