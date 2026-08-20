from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation.runner import main
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios.common.fixture_models import (
    FixtureBuildResult,
    FixtureValidationContext,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.page_readback import SPECIAL_PAGE_TITLE


def _manifest() -> dict:
    notebook = {
        "id": "notebook",
        "resource_type": "notebook",
        "name": "__hierarchy-navigation-source__",
        "path": "Notebook",
        "parent_id": None,
    }

    def item(key, object_id, resource_type, path, parent_id, **extra):
        name = str(extra.pop("display_name", path.rsplit("/", 1)[-1]))
        return key, {
            "id": object_id,
            "resource_type": resource_type,
            "name": name,
            "title": name if resource_type == "page" else None,
            "path": path,
            "parent_id": parent_id,
            "notebook_id": notebook["id"],
            **extra,
        }

    structure = dict(
        [
            item(
                "navigation_root_section",
                "root-section",
                "section",
                "Notebook/Navigation-Root-Section",
                "notebook",
            ),
            item(
                "navigation_group",
                "group",
                "section_group",
                "Notebook/Navigation-Group",
                "notebook",
            ),
            item(
                "navigation_inner_group",
                "inner-group",
                "section_group",
                "Notebook/Navigation-Group/Navigation-Inner-Group",
                "group",
            ),
            item(
                "navigation_section",
                "section",
                "section",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section",
                "inner-group",
            ),
            item(
                "navigation_section_sibling",
                "section-sibling",
                "section",
                "Notebook/Navigation-Group/Navigation-Group-Section",
                "group",
            ),
            item(
                "navigation_parent_page",
                "parent",
                "page",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section/"
                + SPECIAL_PAGE_TITLE,
                "section",
                display_name=SPECIAL_PAGE_TITLE,
                section_id="section",
                page_level=1,
                parent_page_id=None,
            ),
            item(
                "navigation_child_page",
                "child",
                "page",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section/Navigation-Child",
                "section",
                section_id="section",
                page_level=2,
                parent_page_id="parent",
            ),
            item(
                "navigation_grandchild_page",
                "grandchild",
                "page",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section/Navigation-Grandchild",
                "section",
                section_id="section",
                page_level=3,
                parent_page_id="child",
            ),
            item(
                "navigation_child_page_sibling",
                "child-sibling",
                "page",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section/Navigation-Child-Sibling",
                "section",
                section_id="section",
                page_level=2,
                parent_page_id="parent",
            ),
            item(
                "navigation_root_page_sibling",
                "root-sibling",
                "page",
                "Notebook/Navigation-Group/Navigation-Inner-Group/Navigation-Target-Section/Navigation-Root-Sibling",
                "section",
                section_id="section",
                page_level=1,
                parent_page_id=None,
            ),
        ]
    )
    browse = {
        "id": "browse-notebook",
        "resource_type": "notebook",
        "name": "__hierarchy-navigation-browse-b__",
        "path": "Browse Notebook",
        "parent_id": None,
    }
    return {
        "notebook": notebook,
        "notebooks": {"source": notebook, "browse-b": browse},
        "structure": structure,
    }


def _node(item, children=()):
    return {"item": item, "children": list(children)}


def _tree(manifest: dict) -> dict:
    s = manifest["structure"]
    grandchild = _node(s["navigation_grandchild_page"])
    child = _node(s["navigation_child_page"], [grandchild])
    child_sibling = _node(s["navigation_child_page_sibling"])
    parent = _node(s["navigation_parent_page"], [child, child_sibling])
    root_sibling = _node(s["navigation_root_page_sibling"])
    section = _node(s["navigation_section"], [parent, root_sibling])
    section_sibling = _node(s["navigation_section_sibling"])
    inner = _node(s["navigation_inner_group"], [section])
    group = _node(s["navigation_group"], [section_sibling, inner])
    root_section = _node(s["navigation_root_section"])
    return _node(manifest["notebook"], [root_section, group])


def _find(node: dict, object_id: str) -> dict | None:
    if node["item"]["id"] == object_id:
        return node
    for child in node["children"]:
        found = _find(child, object_id)
        if found is not None:
            return found
    return None


class _NavigationClient:
    def __init__(self, manifest: dict, *, break_indentation: bool = False) -> None:
        self.manifest = manifest
        self.break_indentation = break_indentation
        self.calls: list[str] = []

    async def call_tool(self, name, arguments, retry_read=False):
        self.calls.append(name)
        s = self.manifest["structure"]
        if name == "list_notebooks":
            items = [
                self.manifest["notebooks"]["source"],
                self.manifest["notebooks"]["browse-b"],
            ]
            return {"items": items, "count": len(items)}
        complete = _tree(self.manifest)
        if self.break_indentation:
            parent_node = _find(complete, "parent")
            assert parent_node is not None
            parent_node["children"].reverse()
        if name == "expand_hierarchy":
            found = _find(complete, arguments["root_id"])
            assert found is not None
            if arguments.get("max_depth") == 1:
                return {
                    "tree": _node(
                        found["item"],
                        [_node(child["item"]) for child in found["children"]],
                    )
                }
            return {"tree": found}
        if name == "expand_notebook":
            return {
                "tree": _node(
                    self.manifest["notebook"],
                    [
                        _node(s["navigation_root_section"]),
                        _node(
                            s["navigation_group"],
                            [
                                _node(s["navigation_section_sibling"]),
                                _node(
                                    s["navigation_inner_group"],
                                    [_node(s["navigation_section"])],
                                ),
                            ],
                        ),
                    ],
                )
            }
        if name == "expand_section_group":
            return {
                "tree": _node(
                    s["navigation_group"],
                    [
                        _node(s["navigation_section_sibling"]),
                        _node(
                            s["navigation_inner_group"],
                            [_node(s["navigation_section"])],
                        ),
                    ],
                )
            }
        if name == "expand_section":
            found = _find(complete, s["navigation_section"]["id"])
            assert found is not None
            return {"tree": found}
        if name == "expand_page":
            found = _find(complete, s["navigation_parent_page"]["id"])
            assert found is not None
            return {"tree": found}
        if name == "get_hierarchy_path":
            target = s["navigation_parent_page"]
            chain = [
                self.manifest["notebook"],
                s["navigation_group"],
                s["navigation_inner_group"],
                s["navigation_section"],
                target,
            ]
            return {
                "item": target,
                "path": target["path"],
                "path_segments": [
                    {
                        "resource_type": item["resource_type"],
                        "id": item["id"],
                        "name": item.get("title") or item.get("name"),
                    }
                    for item in chain
                ],
                "ancestors": chain[:-1],
            }
        raise AssertionError((name, arguments))


def test_hierarchy_navigation_recipe_is_fresh_only_and_policy_is_least_privilege(
    capsys,
) -> None:
    scenario = SCENARIO_REGISTRY.get("hierarchy-navigation")
    recipe = scenario.fixture_recipe
    assert scenario.included_in_all is False
    assert recipe.supports_cache is False
    assert "Section below a SectionGroup" in recipe.fresh_only_reason
    assert recipe.recipe_version == 4
    assert [role.role for role in recipe.cache_identity.notebook_roles] == [
        "browse-b",
        "source",
    ]
    assert {
        "list_notebooks",
        "expand_notebook",
        "expand_section_group",
        "expand_section",
        "expand_page",
        "expand_hierarchy",
        "get_hierarchy_path",
    } <= scenario.spec.tool_allowlist
    assert not any(tool.startswith("query_") for tool in scenario.spec.tool_allowlist)
    assert "get_parent" not in scenario.spec.tool_allowlist
    assert {
        tool for tool in scenario.spec.tool_allowlist if tool.startswith("list_")
    } == {"list_notebooks"}
    assert scenario.spec.policy.writes_enabled is True
    assert scenario.spec.policy.deletes_enabled is False
    assert scenario.spec.policy.create_enabled is True

    assert main(["hierarchy-navigation", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_execution_prohibited"] is True
    assert payload["server_started"] is False
    assert payload["scenario_spec"]["execution_contract"]["included_in_all"] is False


def test_hierarchy_navigation_recipe_proves_three_page_levels() -> None:
    scenario = SCENARIO_REGISTRY.get("hierarchy-navigation")
    manifest = _manifest()
    structure = manifest["structure"]
    snapshot = {
        "notebook_id": manifest["notebook"]["id"],
        "items": list(structure.values()),
        "page_hashes": {
            item["id"]: f"hash-{item['id']}"
            for item in structure.values()
            if item["resource_type"] == "page"
        },
    }
    checks = scenario.fixture_recipe.validate(
        FixtureValidationContext(SimpleNamespace(), snapshot),
        FixtureBuildResult(structure, {}),
    )
    assert "Page levels 1/2/3 derive the exact branched indentation tree" in checks


def test_hierarchy_navigation_recipe_snapshot_uses_expand_only() -> None:
    manifest = _manifest()
    client = _NavigationClient(manifest)

    snapshot = asyncio.run(
        SCENARIO_REGISTRY.get("hierarchy-navigation").fixture_recipe.capture_snapshot(
            client, manifest["notebook"]["id"]
        )
    )

    assert client.calls == ["expand_hierarchy"]
    assert snapshot["metadata_source"] == "expand_hierarchy"
    assert len(snapshot["items"]) == len(manifest["structure"]) + 1


def test_hierarchy_navigation_runtime_covers_list_expand_and_exact_path(tmp_path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "hierarchy-navigation").mkdir(parents=True)
    manifest = _manifest()
    client = _NavigationClient(manifest)
    result = asyncio.run(
        SCENARIO_REGISTRY.get("hierarchy-navigation").execute(
            SimpleNamespace(),
            RuntimeOptions(run_dir, 180, True, False),
            manifest,
            client=client,
            fixture_result={"status": "prepared"},
        )
    )
    assert result["status"] == "passed"
    assert result["page_indentation_tree_passed"] is True
    assert result["list_notebooks_contract_passed"] is True
    assert result["typed_expand_contract_passed"] is True
    assert result["generic_four_root_contract_passed"] is True
    assert result["hierarchy_path_segments_passed"] is True
    assert set(client.calls) == {
        "list_notebooks",
        "expand_notebook",
        "expand_section_group",
        "expand_section",
        "expand_page",
        "expand_hierarchy",
        "get_hierarchy_path",
    }
    evidence = run_dir / "scenarios" / "hierarchy-navigation"
    assert (evidence / "list-notebooks.json").exists()
    assert (evidence / "expand-hierarchy-notebook.json").exists()
    assert (evidence / "expand-hierarchy-page-depth-boundary.json").exists()
    assert (evidence / "get-hierarchy-path.json").exists()

    tree_evidence = json.loads(
        (evidence / "expand-hierarchy-notebook.json").read_text(encoding="utf-8")
    )
    parent_node = _find(tree_evidence["tree"], "parent")
    assert parent_node is not None
    assert parent_node["item"]["page_level"] == 1
    assert [child["item"]["id"] for child in parent_node["children"]] == [
        "child",
        "child-sibling",
    ]
    assert parent_node["children"][0]["children"][0]["item"]["id"] == (
        "grandchild"
    )
    assert parent_node["children"][0]["children"][0]["item"]["page_level"] == 3
    path_evidence = json.loads(
        (evidence / "get-hierarchy-path.json").read_text(encoding="utf-8")
    )
    assert path_evidence["legacy_path_display_only"] is True
    assert path_evidence["path_segments"][-1]["name"] == SPECIAL_PAGE_TITLE
    assert path_evidence["special_title_preserved"] is True


def test_hierarchy_navigation_rejects_wrong_page_child_order(tmp_path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "hierarchy-navigation").mkdir(parents=True)
    manifest = _manifest()
    with pytest.raises(InvariantFailure, match="page_level as the exact"):
        asyncio.run(
            SCENARIO_REGISTRY.get("hierarchy-navigation").execute(
                SimpleNamespace(),
                RuntimeOptions(run_dir, 180, True, False),
                manifest,
                client=_NavigationClient(manifest, break_indentation=True),
                fixture_result={"status": "prepared"},
            )
        )
