"""HUMAN-GATED validation for parent, path, and indentation-tree navigation."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import scenario_dir, write_json
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.hierarchy_navigation import RECIPE


@SCENARIO_REGISTRY.register
class HierarchyNavigationScenario(Scenario):
    name = "hierarchy-navigation"
    fixture_recipe = RECIPE
    included_in_all = False
    help_text = (
        "HUMAN-GATED: validate get_parent/get_path container ancestry and prove "
        "that get_tree projects Page page_level indentation as a nested tree."
    )

    @staticmethod
    def _node_by_id(node: dict[str, Any], object_id: str) -> dict[str, Any] | None:
        item = node.get("item")
        if isinstance(item, dict) and str(item.get("id", "")) == object_id:
            return node
        for child in node.get("children", []):
            if isinstance(child, dict):
                found = HierarchyNavigationScenario._node_by_id(child, object_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _child_ids(node: dict[str, Any]) -> list[str]:
        return [
            str(child.get("item", {}).get("id", ""))
            for child in node.get("children", [])
            if isinstance(child, dict) and isinstance(child.get("item"), dict)
        ]

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        if client is None:
            raise RunnerFailure(
                "Hierarchy navigation scenario requires its active scenario MCP client."
            )
        out = scenario_dir(options.run_dir, self.name)
        notebook = dict(manifest["notebook"])
        structure = {key: dict(value) for key, value in manifest["structure"].items()}
        group = structure["navigation_group"]
        section = structure["navigation_section"]
        parent_page = structure["navigation_parent_page"]
        child_page = structure["navigation_child_page"]
        grandchild_page = structure["navigation_grandchild_page"]
        child_sibling = structure["navigation_child_page_sibling"]
        root_sibling = structure["navigation_root_page_sibling"]

        parent_cases: list[dict[str, Any]] = []
        for label, item, expected_parent in (
            ("section-group-to-notebook", group, notebook),
            ("section-to-section-group", section, group),
            ("indented-page-to-container-section", grandchild_page, section),
        ):
            response = await client.call_tool(
                "get_parent", {"object_id": str(item["id"])}, retry_read=False
            )
            if (
                response.get("item", {}).get("id") != item.get("id")
                or response.get("parent_id") != expected_parent.get("id")
                or response.get("parent", {}).get("id") != expected_parent.get("id")
                or response.get("parent", {}).get("resource_type")
                != expected_parent.get("resource_type")
            ):
                raise InvariantFailure(f"get_parent case {label} returned wrong ancestry.")
            parent_cases.append({"label": label, "response": response})
        write_json(out / "get-parent-cases.json", {"cases": parent_cases})

        path_response = await client.call_tool(
            "get_path",
            {"object_id": str(grandchild_page["id"])},
            retry_read=False,
        )
        ancestor_ids = [
            str(item.get("id", "")) for item in path_response.get("ancestors", [])
        ]
        expected_container_ancestors = [
            str(notebook["id"]),
            str(group["id"]),
            str(section["id"]),
        ]
        if (
            path_response.get("item", {}).get("id") != grandchild_page.get("id")
            or path_response.get("path") != grandchild_page.get("path")
            or ancestor_ids != expected_container_ancestors
            or str(parent_page["id"]) in ancestor_ids
            or str(child_page["id"]) in ancestor_ids
        ):
            raise InvariantFailure(
                "get_path did not preserve the Page's exact container ancestry."
            )
        write_json(out / "get-path-page.json", path_response)

        tree_response = await client.call_tool(
            "get_tree",
            {"root_id": str(notebook["id"]), "max_depth": 8},
            retry_read=False,
        )
        tree = tree_response.get("tree", {})
        parent_node = self._node_by_id(tree, str(parent_page["id"]))
        child_node = self._node_by_id(tree, str(child_page["id"]))
        grandchild_node = self._node_by_id(tree, str(grandchild_page["id"]))
        child_sibling_node = self._node_by_id(tree, str(child_sibling["id"]))
        root_sibling_node = self._node_by_id(tree, str(root_sibling["id"]))
        if any(
            node is None
            for node in (
                parent_node,
                child_node,
                grandchild_node,
                child_sibling_node,
                root_sibling_node,
            )
        ):
            raise InvariantFailure("get_tree omitted a manifest-bound Page node.")
        if (
            self._child_ids(parent_node)
            != [str(child_page["id"]), str(child_sibling["id"])]
            or self._child_ids(child_node) != [str(grandchild_page["id"])]
            or self._child_ids(grandchild_node)
            or self._child_ids(child_sibling_node)
            or self._child_ids(root_sibling_node)
            or int(parent_node["item"].get("page_level", 0)) != 1
            or int(child_node["item"].get("page_level", 0)) != 2
            or int(grandchild_node["item"].get("page_level", 0)) != 3
            or int(child_sibling_node["item"].get("page_level", 0)) != 2
            or int(root_sibling_node["item"].get("page_level", 0)) != 1
        ):
            raise InvariantFailure(
                "get_tree did not project page_level as the exact Page indentation tree."
            )
        write_json(out / "get-tree-notebook.json", tree_response)

        bounded_response = await client.call_tool(
            "get_tree",
            {"root_id": str(parent_page["id"]), "max_depth": 1},
            retry_read=False,
        )
        bounded_tree = bounded_response.get("tree", {})
        bounded_children = bounded_tree.get("children", [])
        if (
            bounded_tree.get("item", {}).get("id") != parent_page.get("id")
            or self._child_ids(bounded_tree)
            != [str(child_page["id"]), str(child_sibling["id"])]
            or any(child.get("children") for child in bounded_children)
        ):
            raise InvariantFailure(
                "get_tree max_depth boundary did not stop below direct Page children."
            )
        write_json(out / "get-tree-page-depth-boundary.json", bounded_response)

        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "get_parent_cases_passed": len(parent_cases),
            "get_path_container_ancestry_passed": True,
            "page_indentation_tree_passed": True,
            "max_depth_boundary_passed": True,
            "filesystem_deleted": False,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["HierarchyNavigationScenario"]
