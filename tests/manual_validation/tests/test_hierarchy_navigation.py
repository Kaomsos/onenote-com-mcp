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


def _manifest() -> dict:
    notebook = {
        "id": "notebook",
        "resource_type": "notebook",
        "name": "__hierarchy-navigation-source__",
        "path": "Notebook",
        "parent_id": None,
    }

    def item(key, object_id, resource_type, path, parent_id, **extra):
        name = path.rsplit("/", 1)[-1]
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
                "navigation_group",
                "group",
                "section_group",
                "Notebook/Navigation-Group",
                "notebook",
            ),
            item(
                "navigation_section",
                "section",
                "section",
                "Notebook/Navigation-Group/Navigation-Section",
                "group",
            ),
            item(
                "navigation_section_sibling",
                "section-sibling",
                "section",
                "Notebook/Navigation-Group/Navigation-Section-Sibling",
                "group",
            ),
            item(
                "navigation_parent_page",
                "parent",
                "page",
                "Notebook/Navigation-Group/Navigation-Section/Navigation-Parent",
                "section",
                section_id="section",
                page_level=1,
                parent_page_id=None,
            ),
            item(
                "navigation_child_page",
                "child",
                "page",
                "Notebook/Navigation-Group/Navigation-Section/Navigation-Child",
                "section",
                section_id="section",
                page_level=2,
                parent_page_id="parent",
            ),
            item(
                "navigation_grandchild_page",
                "grandchild",
                "page",
                "Notebook/Navigation-Group/Navigation-Section/Navigation-Grandchild",
                "section",
                section_id="section",
                page_level=3,
                parent_page_id="child",
            ),
            item(
                "navigation_child_page_sibling",
                "child-sibling",
                "page",
                "Notebook/Navigation-Group/Navigation-Section/Navigation-Child-Sibling",
                "section",
                section_id="section",
                page_level=2,
                parent_page_id="parent",
            ),
            item(
                "navigation_root_page_sibling",
                "root-sibling",
                "page",
                "Notebook/Navigation-Group/Navigation-Section/Navigation-Root-Sibling",
                "section",
                section_id="section",
                page_level=1,
                parent_page_id=None,
            ),
        ]
    )
    return {"notebook": notebook, "structure": structure}


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
    group = _node(s["navigation_group"], [section, section_sibling])
    return _node(manifest["notebook"], [group])


class _NavigationClient:
    def __init__(self, manifest: dict, *, break_indentation: bool = False) -> None:
        self.manifest = manifest
        self.break_indentation = break_indentation

    async def call_tool(self, name, arguments, retry_read=False):
        s = self.manifest["structure"]
        by_id = {
            item["id"]: item
            for item in [self.manifest["notebook"], *s.values()]
        }
        if name == "get_parent":
            item = by_id[arguments["object_id"]]
            parent = by_id[item["parent_id"]]
            return {"item": item, "parent": parent, "parent_id": parent["id"]}
        if name == "get_path":
            item = by_id[arguments["object_id"]]
            return {
                "item": item,
                "path": item["path"],
                "ancestors": [
                    self.manifest["notebook"],
                    s["navigation_group"],
                    s["navigation_section"],
                ],
            }
        if name == "get_tree":
            if arguments["root_id"] == self.manifest["notebook"]["id"]:
                tree = _tree(self.manifest)
                if self.break_indentation:
                    parent = tree["children"][0]["children"][0]["children"][0]
                    parent["children"].reverse()
                return {"tree": tree}
            if arguments["root_id"] == s["navigation_parent_page"]["id"]:
                return {
                    "tree": _node(
                        s["navigation_parent_page"],
                        [
                            _node(s["navigation_child_page"]),
                            _node(s["navigation_child_page_sibling"]),
                        ],
                    )
                }
        raise AssertionError((name, arguments))


def test_hierarchy_navigation_recipe_and_policy_are_cacheable_and_least_privilege(
    capsys,
) -> None:
    scenario = SCENARIO_REGISTRY.get("hierarchy-navigation")
    recipe = scenario.fixture_recipe
    assert scenario.included_in_all is False
    assert recipe.supports_cache is True
    assert recipe.recipe_version == 1
    assert {"get_parent", "get_path", "get_tree"} <= scenario.spec.tool_allowlist
    assert {"query_section_group", "query_section", "query_page"} <= (
        scenario.spec.tool_allowlist
    )
    assert not any(
        tool.startswith("list_") for tool in scenario.spec.tool_allowlist
    )
    assert scenario.spec.policy.writes_enabled is True
    assert scenario.spec.policy.deletes_enabled is False
    assert scenario.spec.policy.raw_xml_enabled is False

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


def test_hierarchy_navigation_runtime_covers_parent_path_tree_and_depth(tmp_path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "hierarchy-navigation").mkdir(parents=True)
    manifest = _manifest()
    result = asyncio.run(
        SCENARIO_REGISTRY.get("hierarchy-navigation").execute(
            SimpleNamespace(),
            RuntimeOptions(run_dir, 180, True, False),
            manifest,
            client=_NavigationClient(manifest),
            fixture_result={"status": "prepared"},
        )
    )
    assert result["status"] == "passed"
    assert result["get_parent_cases_passed"] == 3
    assert result["page_indentation_tree_passed"] is True
    evidence = run_dir / "scenarios" / "hierarchy-navigation"
    assert (evidence / "get-parent-cases.json").exists()
    assert (evidence / "get-path-page.json").exists()
    assert (evidence / "get-tree-notebook.json").exists()
    assert (evidence / "get-tree-page-depth-boundary.json").exists()
    parent_evidence = json.loads(
        (evidence / "get-parent-cases.json").read_text(encoding="utf-8")
    )
    page_parent_case = next(
        case
        for case in parent_evidence["cases"]
        if case["label"] == "indented-page-to-container-section"
    )
    assert page_parent_case["response"]["item"]["id"] == "grandchild"
    assert page_parent_case["response"]["parent"]["id"] == "section"

    path_evidence = json.loads(
        (evidence / "get-path-page.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in path_evidence["ancestors"]] == [
        "notebook",
        "group",
        "section",
    ]

    tree_evidence = json.loads(
        (evidence / "get-tree-notebook.json").read_text(encoding="utf-8")
    )
    parent_node = (
        tree_evidence["tree"]["children"][0]["children"][0]["children"][0]
    )
    assert parent_node["item"]["page_level"] == 1
    assert [child["item"]["id"] for child in parent_node["children"]] == [
        "child",
        "child-sibling",
    ]
    assert parent_node["children"][0]["children"][0]["item"]["id"] == (
        "grandchild"
    )
    assert parent_node["children"][0]["children"][0]["item"]["page_level"] == 3


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
