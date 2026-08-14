"""HUMAN-GATED validation for the List/Expand hierarchy navigation family."""

from __future__ import annotations

import argparse
import json
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
        "HUMAN-GATED: validate the List/Expand hierarchy navigation contract, "
        "typed boundaries, general expansion, and Page indentation tree."
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

    @staticmethod
    def _flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
        item = node.get("item")
        result = [item] if isinstance(item, dict) else []
        children = node.get("children")
        if not isinstance(children, list):
            raise InvariantFailure("Expand tree node omitted its children array.")
        for child in children:
            if not isinstance(child, dict):
                raise InvariantFailure("Expand tree contains a non-object child node.")
            result.extend(HierarchyNavigationScenario._flatten(child))
        return result

    @classmethod
    def _edges(cls, node: dict[str, Any]) -> set[tuple[str, str]]:
        edges: set[tuple[str, str]] = set()
        parent_id = str(node.get("item", {}).get("id", ""))
        for child in node.get("children", []):
            child_id = str(child.get("item", {}).get("id", ""))
            edges.add((parent_id, child_id))
            edges.update(cls._edges(child))
        return edges

    @classmethod
    def _validate_tree(cls, node: dict[str, Any], expected_root_id: str) -> None:
        if node.get("item", {}).get("id") != expected_root_id:
            raise InvariantFailure("Expand tree returned the wrong exact root ID.")
        items = cls._flatten(node)
        ids = [str(item.get("id", "")) for item in items]
        if any(not object_id for object_id in ids) or len(ids) != len(set(ids)):
            raise InvariantFailure("Expand tree contains a missing or duplicate object ID.")

    @staticmethod
    def _audit_operations(path, start: int) -> tuple[list[str], int]:
        if not path.exists():
            return [], start
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [str(record.get("operation", "")) for record in records[start:]], len(records)

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
        notebooks = manifest.get("notebooks")
        if not isinstance(notebooks, dict):
            notebooks = {"source": manifest["notebook"]}
        notebook = dict(notebooks["source"])
        browse_notebook = dict(notebooks.get("browse-b", notebook))
        structure = {key: dict(value) for key, value in manifest["structure"].items()}
        group = structure["navigation_group"]
        inner_group = structure["navigation_inner_group"]
        root_section = structure["navigation_root_section"]
        section = structure["navigation_section"]
        group_section = structure["navigation_section_sibling"]
        parent_page = structure["navigation_parent_page"]
        child_page = structure["navigation_child_page"]
        grandchild_page = structure["navigation_grandchild_page"]
        child_sibling = structure["navigation_child_page_sibling"]
        root_sibling = structure["navigation_root_page_sibling"]

        audit_path = getattr(client, "run_dir", None)
        audit_file = audit_path / "bridge-calls.jsonl" if audit_path is not None else None
        audit_cursor = 0
        if audit_file is not None and audit_file.exists():
            audit_cursor = len(
                [
                    line
                    for line in audit_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            )

        listed = await client.call_tool("list_notebooks", {}, retry_read=False)
        listed_ids = [str(item.get("id", "")) for item in listed.get("items", [])]
        role_ids = {str(notebook["id"]), str(browse_notebook["id"])}
        if (
            listed.get("count") != len(listed_ids)
            or len(listed_ids) != len(set(listed_ids))
            or any(not object_id for object_id in listed_ids)
            or not role_ids <= set(listed_ids)
        ):
            raise InvariantFailure(
                "list_notebooks did not return one unique item per open fixture Notebook."
            )
        write_json(
            out / "list-notebooks.json",
            {
                "listed_count": len(listed_ids),
                "listed_ids": listed_ids,
                "fixture_role_ids": sorted(role_ids),
                "all_fixture_notebooks_present": True,
                "unique_nonempty_ids": True,
            },
        )

        typed_notebook = (
            await client.call_tool(
                "expand_notebook", {"id": str(notebook["id"])}, retry_read=False
            )
        )["tree"]
        typed_group = (
            await client.call_tool(
                "expand_section_group", {"id": str(group["id"])}, retry_read=False
            )
        )["tree"]
        typed_section = (
            await client.call_tool(
                "expand_section", {"id": str(section["id"])}, retry_read=False
            )
        )["tree"]
        typed_page = (
            await client.call_tool(
                "expand_page", {"id": str(parent_page["id"])}, retry_read=False
            )
        )["tree"]
        for tree, root_id in (
            (typed_notebook, str(notebook["id"])),
            (typed_group, str(group["id"])),
            (typed_section, str(section["id"])),
            (typed_page, str(parent_page["id"])),
        ):
            self._validate_tree(tree, root_id)
        outer_node = self._node_by_id(typed_notebook, str(group["id"]))
        inner_node = self._node_by_id(typed_notebook, str(inner_group["id"]))
        if (
            self._child_ids(typed_notebook)
            != [str(root_section["id"]), str(group["id"])]
            or outer_node is None
            or self._child_ids(outer_node)
            != [str(group_section["id"]), str(inner_group["id"])]
            or inner_node is None
            or self._child_ids(inner_node) != [str(section["id"])]
            or any(
                self._node_by_id(typed_notebook, str(item["id"])).get("children")
                for item in self._flatten(typed_notebook)
                if item.get("resource_type") == "section"
                and self._node_by_id(typed_notebook, str(item["id"])) is not None
            )
        ):
            raise InvariantFailure("expand_notebook violated its Section-leaf boundary or order.")
        if (
            str(root_section["id"])
            in {str(item["id"]) for item in self._flatten(typed_group)}
            or self._child_ids(typed_group)
            != [str(group_section["id"]), str(inner_group["id"])]
        ):
            raise InvariantFailure("expand_section_group escaped its exact Group boundary.")
        write_json(
            out / "typed-expand-trees.json",
            {
                "notebook": typed_notebook,
                "section_group": typed_group,
                "section": typed_section,
                "page": typed_page,
            },
        )

        general_trees: dict[str, dict[str, Any]] = {}
        for label, item in (
            ("notebook", notebook),
            ("section_group", group),
            ("section", section),
            ("page", parent_page),
        ):
            response = await client.call_tool(
                "expand_hierarchy",
                {"root_id": str(item["id"]), "max_depth": 8},
                retry_read=False,
            )
            general_trees[label] = response["tree"]
            self._validate_tree(response["tree"], str(item["id"]))
        tree_response = {"tree": general_trees["notebook"]}
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
            raise InvariantFailure("expand_hierarchy omitted a manifest-bound Page node.")
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
                "expand_hierarchy did not project page_level as the exact Page indentation tree."
            )
        write_json(out / "expand-hierarchy-notebook.json", tree_response)

        for label, typed in (
            ("notebook", typed_notebook),
            ("section_group", typed_group),
            ("section", typed_section),
            ("page", typed_page),
        ):
            general = general_trees[label]
            if not self._edges(typed) <= self._edges(general):
                raise InvariantFailure(
                    f"expand_hierarchy relationships diverged from typed {label} Expand."
                )
        if (
            self._edges(typed_section) != self._edges(general_trees["section"])
            or self._edges(typed_page) != self._edges(general_trees["page"])
        ):
            raise InvariantFailure(
                "Section/Page typed Expand diverged from the complete generic expansion."
            )

        bounded_response = await client.call_tool(
            "expand_hierarchy",
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
                "expand_hierarchy max_depth boundary did not stop below direct Page children."
            )
        write_json(out / "expand-hierarchy-page-depth-boundary.json", bounded_response)

        audit_verified = audit_file is None
        operations: list[str] = []
        if audit_file is not None:
            operations, _ = self._audit_operations(audit_file, audit_cursor)
            if not operations or set(operations) != {"get_hierarchy"}:
                raise InvariantFailure(
                    "Hierarchy browsing audit contains a non-hierarchy metadata operation."
                )
            audit_verified = True
            write_json(
                out / "hierarchy-browsing-audit.json",
                {"operations": operations, "page_body_reads": False},
            )

        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "page_indentation_tree_passed": True,
            "list_notebooks_contract_passed": True,
            "typed_expand_contract_passed": True,
            "generic_four_root_contract_passed": True,
            "max_depth_boundary_passed": True,
            "hierarchy_metadata_only_audit_passed": audit_verified,
            "hierarchy_metadata_operation_count": len(operations),
            "filesystem_deleted": False,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["HierarchyNavigationScenario"]
