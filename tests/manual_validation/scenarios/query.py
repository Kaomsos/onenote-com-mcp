"""HUMAN-GATED live validation for typed metadata Query scopes and pagination."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Mapping

from ..lifecycle import NotebookLifecycleWrapper
from ..mcp_stdio_client import MCPStdioClient
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import scenario_dir, write_json
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.query import RECIPE


@SCENARIO_REGISTRY.register
class QueryScenario(Scenario):
    name = "query"
    fixture_recipe = RECIPE
    included_in_all = True
    timeout_default = 300
    requires_lifecycle_wrappers = True
    help_text = (
        "HUMAN-GATED: validate four typed hierarchy metadata Query tools, native "
        "root/start-node scopes, live pagination, and closed-Notebook exclusion."
    )

    @staticmethod
    def _ids(result: dict[str, Any]) -> list[str]:
        return [str(item.get("id", "")) for item in result.get("items", [])]

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

    async def execute_with_lifecycle(
        self,
        args,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
        wrappers: Mapping[str, NotebookLifecycleWrapper],
    ) -> dict[str, Any]:
        if client is None:
            raise RunnerFailure("Typed Query scenario requires its active scenario MCP client.")
        out = scenario_dir(options.run_dir, self.name)
        structure = {key: dict(value) for key, value in manifest["structure"].items()}
        notebooks = manifest["notebooks"]
        evidence: dict[str, Any] = {"requests": []}
        metadata_items: dict[str, list[dict[str, Any]]] = {}

        for role in ("source", "query-b"):
            notebook_id = str(notebooks[role]["id"])
            metadata_items[role] = [dict(notebooks[role])] + [
                dict(item)
                for item in structure.values()
                if str(item.get("notebook_id", "")) == notebook_id
            ]
            write_json(
                out / f"fixture-metadata-{role}.json",
                {
                    "notebook_id": notebook_id,
                    "items": metadata_items[role],
                    "source": "validated scenario fixture manifest",
                },
            )

        metadata_by_id = {
            str(item.get("id", "")): item
            for items in metadata_items.values()
            for item in items
            if item.get("id")
        }
        open_ids: set[str] = set()
        baseline_notebook_count: int | None = None
        baseline_total_matches: int | None = None
        offset = 0
        while True:
            open_catalog = await client.call_tool(
                "query_notebook",
                {"offset": offset, "page_size": 200},
                retry_read=False,
            )
            items = open_catalog.get("items", [])
            response_scope = open_catalog.get("scope", {})
            page_count = int(open_catalog.get("count", -1))
            total_matches = int(open_catalog.get("total_matches", -1))
            notebook_count = int(response_scope.get("notebook_count", -1))
            if (
                open_catalog.get("resource_type") != "notebook"
                or open_catalog.get("query_kind") != "hierarchy_metadata"
                or response_scope.get("mode") != "root"
                or page_count != len(items)
                or total_matches != notebook_count
            ):
                raise InvariantFailure(
                    "Typed Query open-Notebook baseline returned an invalid envelope."
                )
            if baseline_notebook_count is None:
                baseline_notebook_count = notebook_count
                baseline_total_matches = total_matches
            elif (
                notebook_count != baseline_notebook_count
                or total_matches != baseline_total_matches
            ):
                raise InvariantFailure(
                    "Typed Query open-Notebook baseline changed during pagination."
                )
            page_ids = {
                str(item.get("id", ""))
                for item in items
                if isinstance(item, dict) and item.get("id")
            }
            if len(page_ids) != len(items) or open_ids.intersection(page_ids):
                raise InvariantFailure(
                    "Typed Query open-Notebook baseline contains missing or duplicate IDs."
                )
            open_ids.update(page_ids)
            if not open_catalog.get("has_more"):
                break
            next_offset = open_catalog.get("next_offset")
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise InvariantFailure(
                    "Typed Query open-Notebook baseline returned invalid pagination."
                )
            offset = next_offset
        fixture_notebook_ids = {
            str(notebooks["source"]["id"]),
            str(notebooks["query-b"]["id"]),
        }
        if baseline_notebook_count is None:
            raise InvariantFailure("Typed Query open-Notebook baseline is missing.")
        if (
            baseline_notebook_count != len(open_ids)
            or not fixture_notebook_ids.issubset(open_ids)
        ):
            raise InvariantFailure(
                "Typed Query open-Notebook baseline does not contain both fixture roles."
            )
        write_json(
            out / "open-notebook-baseline.json",
            {
                "schema_version": 1,
                "open_notebook_count": baseline_notebook_count,
                "fixture_notebook_count": len(fixture_notebook_ids),
                "all_fixture_notebooks_present": True,
                "unrelated_notebook_identity_persisted": False,
            },
        )
        independent_expected: dict[str, dict[str, Any]] = {}
        for key, manifest_item in structure.items():
            object_id = str(manifest_item.get("id", ""))
            metadata_item = metadata_by_id.get(object_id)
            if metadata_item is None:
                raise InvariantFailure(
                    f"Validated fixture metadata is missing fixture key {key}."
                )
            if metadata_item.get("resource_type") != manifest_item.get("resource_type"):
                raise InvariantFailure(
                    f"Validated fixture metadata mistypes fixture key {key}."
                )
            independent_expected[key] = {
                field: metadata_item.get(field)
                for field in (
                    "id",
                    "resource_type",
                    "path",
                    "parent_id",
                    "notebook_id",
                    "section_id",
                    "parent_page_id",
                    "page_level",
                    "modified",
                )
            }
        write_json(
            out / "expected-results.json",
            {
                "source": "validated scenario fixture metadata",
                "items": independent_expected,
            },
        )

        audit_path = client.run_dir / "bridge-calls.jsonl"
        audit_cursor = len(
            [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ) if audit_path.exists() else 0

        async def call(
            tool: str,
            arguments: dict[str, Any],
            label: str,
            *,
            expected: dict[str, Any],
            get_hierarchy_calls: int,
        ) -> dict[str, Any]:
            nonlocal audit_cursor
            result = await client.call_tool(tool, arguments, retry_read=False)
            operations, audit_cursor = self._audit_operations(audit_path, audit_cursor)
            if operations != ["get_hierarchy"] * get_hierarchy_calls:
                raise InvariantFailure(
                    f"{label} bridge operations differ: expected {get_hierarchy_calls} "
                    f"GetHierarchy calls, received {operations}."
                )
            actual_ids = self._ids(result)
            fixed_type = {
                "query_notebook": "notebook",
                "query_section_group": "section_group",
                "query_section": "section",
                "query_page": "page",
            }[tool]
            if (
                result.get("resource_type") != fixed_type
                or result.get("query_kind") != "hierarchy_metadata"
                or result.get("pagination_consistency") != "live_hierarchy"
                or result.get("count") != len(actual_ids)
            ):
                raise InvariantFailure(f"{label} returned an invalid typed Query envelope.")
            response_scope = result.get("scope", {})
            requested_scope = arguments.get("scope", {"mode": "root"})
            if response_scope.get("mode") != requested_scope.get("mode"):
                raise InvariantFailure(f"{label} response scope mode differs from its request.")
            if requested_scope.get("mode") == "root":
                if response_scope.get("notebook_count") != expected.get(
                    "notebook_count", baseline_notebook_count
                ):
                    raise InvariantFailure(
                        f"{label} root scope has an incorrect open Notebook count."
                    )
            else:
                start_id = str(requested_scope["start_node_id"])
                start_item = metadata_by_id[start_id]
                expected_notebook_id = (
                    start_id
                    if start_item.get("resource_type") == "notebook"
                    else str(start_item.get("notebook_id", ""))
                )
                if response_scope != {
                    "mode": "start_node",
                    "resource_type": start_item.get("resource_type"),
                    "id": start_id,
                    "path": start_item.get("path"),
                    "notebook_id": expected_notebook_id,
                }:
                    raise InvariantFailure(
                        f"{label} start-node scope does not match validated fixture metadata."
                    )
            expected_ids = expected.get("ids")
            ordered = bool(expected.get("ordered", False))
            if expected_ids is not None:
                matches = (
                    actual_ids == list(expected_ids)
                    if ordered
                    else set(actual_ids) == set(expected_ids)
                    and len(actual_ids) == len(expected_ids)
                )
                if not matches:
                    actual_set = set(actual_ids)
                    expected_set = {str(value) for value in expected_ids}
                    if options.use_cache and expected_set < actual_set:
                        warning = {
                            "schema_version": 1,
                            "label": label,
                            "use_cache": True,
                            "expected_ids": sorted(expected_set),
                            "extra_hit_ids": sorted(actual_set - expected_set),
                            "warning": (
                                "A reused Query fixture token matched another open working copy."
                            ),
                            "query_text_persisted": False,
                        }
                        write_json(out / "cache-query-collision-warning.json", warning)
                        raise InvariantFailure(
                            f"{label} cache_query_collision: another open working copy "
                            "matched the reused fixture token."
                        )
                    raise InvariantFailure(
                        f"{label} result IDs differ from validated fixture metadata."
                    )
            for field in (
                "count", "total_matches", "offset", "page_size", "has_more", "next_offset"
            ):
                if field in expected and result.get(field) != expected[field]:
                    raise InvariantFailure(
                        f"{label} response field {field} differs from independent expectation."
                    )
            record = {
                "label": label,
                "tool": tool,
                "arguments": arguments,
                "fixture_metadata_evidence": [
                    "fixture-metadata-source.json",
                    "fixture-metadata-query-b.json",
                ],
                "independent_expected_evidence": "expected-results.json",
                "expected": expected,
                "bridge_operations": operations,
                "response": result,
            }
            evidence["requests"].append(record)
            write_json(out / "requests-and-responses.json", evidence)
            return result

        source_id = str(notebooks["source"]["id"])
        query_b_id = str(notebooks["query-b"]["id"])
        common_name = str(notebooks["source"]["name"])
        while common_name and not str(notebooks["query-b"]["name"]).endswith(common_name):
            common_name = common_name[1:]
        if len(common_name) < 8:
            raise InvariantFailure("Run-unique Notebook roles do not share a safe query suffix.")

        notebooks_root = await call(
            "query_notebook",
            {"name_contains": common_name, "page_size": 200},
            "notebook-root",
            expected={
                "ids": sorted([source_id, query_b_id]),
                "count": 2,
                "total_matches": 2,
                "notebook_count": baseline_notebook_count,
            },
            get_hierarchy_calls=1,
        )

        parent_title = str(structure["query_parent_page"]["title"])
        if not parent_title.startswith("Q-") or not parent_title.endswith("-Parent"):
            raise InvariantFailure("Fixture Page title lacks its run-unique query token.")
        title_probe = parent_title[: -len("Parent")]

        source_groups = {
            str(structure["query_outer_group"]["id"]),
            str(structure["query_outer_group_sibling"]["id"]),
            str(structure["query_inner_group"]["id"]),
            str(structure["query_inner_group_sibling"]["id"]),
        }
        expected_groups = source_groups | {
            str(structure["query_b_outer_group"]["id"]),
            str(structure["query_b_outer_group_sibling"]["id"]),
            str(structure["query_b_inner_group"]["id"]),
            str(structure["query_b_inner_group_sibling"]["id"]),
        }
        root_groups = await call(
            "query_section_group",
            {"scope": {"mode": "root"}, "name_contains": title_probe, "page_size": 200},
            "groups-root",
            expected={"ids": sorted(expected_groups), "count": 8, "total_matches": 8},
            get_hierarchy_calls=1,
        )
        group_result = await call(
            "query_section_group",
            {"scope": {"mode": "start_node", "start_node_id": source_id}, "page_size": 200},
            "groups-from-notebook",
            expected={"ids": sorted(source_groups), "count": 4, "total_matches": 4},
            get_hierarchy_calls=2,
        )
        nested = await call(
            "query_section_group",
            {"scope": {"mode": "start_node", "start_node_id": str(structure["query_outer_group"]["id"])}, "page_size": 200},
            "groups-from-group",
            expected={
                "ids": sorted(
                    [
                        str(structure["query_inner_group"]["id"]),
                        str(structure["query_inner_group_sibling"]["id"]),
                    ]
                ),
                "count": 2,
                "total_matches": 2,
            },
            get_hierarchy_calls=2,
        )

        deep_id = str(structure["query_deep_section"]["id"])
        expected_sections = {
            deep_id,
            str(structure["query_deep_section_sibling"]["id"]),
            str(structure["query_root_section"]["id"]),
            str(structure["query_root_section_sibling"]["id"]),
            str(structure["query_b_deep_section"]["id"]),
            str(structure["query_b_deep_section_sibling"]["id"]),
            str(structure["query_b_root_section"]["id"]),
            str(structure["query_b_root_section_sibling"]["id"]),
        }
        root_sections = await call(
            "query_section",
            {"scope": {"mode": "root"}, "name_contains": title_probe, "page_size": 200},
            "sections-root",
            expected={"ids": sorted(expected_sections), "count": 8, "total_matches": 8},
            get_hierarchy_calls=1,
        )
        sections = await call(
            "query_section",
            {"scope": {"mode": "start_node", "start_node_id": str(structure["query_outer_group"]["id"])}, "page_size": 200},
            "sections-from-group",
            expected={
                "ids": sorted(
                    [deep_id, str(structure["query_deep_section_sibling"]["id"])]
                ),
                "count": 2,
                "total_matches": 2,
            },
            get_hierarchy_calls=2,
        )

        parent_id = str(structure["query_parent_page"]["id"])
        child_id = str(structure["query_child_page"]["id"])
        child_sibling_id = str(structure["query_child_page_sibling"]["id"])
        source_page_ids = {
            parent_id,
            child_id,
            child_sibling_id,
            str(structure["query_sibling_page"]["id"]),
            str(structure["query_root_page"]["id"]),
            str(structure["query_root_page_sibling"]["id"]),
        }
        notebook_pages = await call(
            "query_page",
            {"scope": {"mode": "start_node", "start_node_id": source_id}, "title_contains": title_probe},
            "pages-from-notebook",
            expected={"ids": sorted(source_page_ids), "count": 6, "total_matches": 6},
            get_hierarchy_calls=2,
        )
        group_pages = await call(
            "query_page",
            {"scope": {"mode": "start_node", "start_node_id": str(structure["query_outer_group"]["id"])}, "title_contains": title_probe},
            "pages-from-group",
            expected={
                "ids": sorted(
                    source_page_ids
                    - {
                        str(structure["query_root_page"]["id"]),
                        str(structure["query_root_page_sibling"]["id"]),
                    }
                ),
                "count": 4,
                "total_matches": 4,
            },
            get_hierarchy_calls=2,
        )
        expected_deep_pages = {
            parent_id,
            child_id,
            child_sibling_id,
            str(structure["query_sibling_page"]["id"]),
        }
        pages = await call(
            "query_page",
            {"scope": {"mode": "start_node", "start_node_id": deep_id}, "section_id": deep_id, "page_size": 200},
            "pages-from-section",
            expected={"ids": sorted(expected_deep_pages), "count": 4, "total_matches": 4},
            get_hierarchy_calls=2,
        )
        child = await call(
            "query_page",
            {"scope": {"mode": "start_node", "start_node_id": deep_id}, "section_id": deep_id, "parent_page_id": parent_id},
            "page-indentation-parent",
            expected={
                "ids": sorted([child_id, child_sibling_id]),
                "count": 2,
                "total_matches": 2,
            },
            get_hierarchy_calls=2,
        )

        modified = str(structure["query_child_page"].get("modified", ""))
        if not modified:
            raise InvariantFailure("Fixture Page lacks modified-time evidence.")
        instant = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        timed = await call(
            "query_page",
            {
                "scope": {"mode": "start_node", "start_node_id": deep_id},
                "title_equals": str(structure["query_child_page"]["title"]),
                "modified_after": (instant - timedelta(seconds=1)).isoformat(),
            },
            "page-modified-after",
            expected={"ids": [child_id], "ordered": True, "count": 1, "total_matches": 1},
            get_hierarchy_calls=2,
        )

        all_page_ids = {
            str(structure[key]["id"])
            for key in (
                "query_parent_page", "query_child_page", "query_sibling_page",
                "query_child_page_sibling", "query_root_page",
                "query_root_page_sibling", "query_b_parent_page",
                "query_b_child_page", "query_b_sibling_page",
                "query_b_child_page_sibling",
                "query_b_root_page", "query_b_root_page_sibling",
            )
        }
        total_pages = len(all_page_ids)
        offset = 0
        paged_ids: list[str] = []
        while True:
            remaining = max(0, total_pages - offset)
            expected_count = min(2, remaining)
            expected_has_more = offset + expected_count < total_pages
            page = await call(
                "query_page",
                {"scope": {"mode": "root"}, "title_contains": title_probe, "offset": offset, "page_size": 2},
                f"pagination-{offset}",
                expected={
                    "count": expected_count,
                    "total_matches": total_pages,
                    "offset": offset,
                    "page_size": 2,
                    "has_more": expected_has_more,
                    "next_offset": offset + expected_count if expected_has_more else None,
                },
                get_hierarchy_calls=1,
            )
            paged_ids.extend(self._ids(page))
            if not page.get("has_more"):
                break
            offset = int(page["next_offset"])
        if set(paged_ids) != all_page_ids or len(paged_ids) != len(all_page_ids):
            raise InvariantFailure("Live pagination did not cover the exact fixture Page set.")
        beyond = await call(
            "query_page",
            {
                "scope": {"mode": "root"},
                "title_contains": title_probe,
                "offset": total_pages + 3,
                "page_size": 2,
            },
            "pagination-beyond-end",
            expected={
                "ids": [],
                "ordered": True,
                "count": 0,
                "total_matches": total_pages,
                "offset": total_pages + 3,
                "page_size": 2,
                "has_more": False,
                "next_offset": None,
            },
            get_hierarchy_calls=1,
        )

        close_result = wrappers["query-b"].close_exact_notebook()
        write_json(out / "closed-query-b.json", close_result)
        audit_cursor = len(
            [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ) if audit_path.exists() else 0
        closed_notebook = await call(
            "query_notebook",
            {"name_equals": str(notebooks["query-b"]["name"])},
            "closed-notebook",
            expected={
                "ids": [],
                "ordered": True,
                "count": 0,
                "total_matches": 0,
                "notebook_count": baseline_notebook_count - 1,
            },
            get_hierarchy_calls=1,
        )
        closed_pages = await call(
            "query_page",
            {"scope": {"mode": "root"}, "title_equals": str(structure["query_b_root_page"]["title"]), "include_recycle_bin": True},
            "closed-notebook-pages-with-recycle",
            expected={
                "ids": [],
                "ordered": True,
                "count": 0,
                "total_matches": 0,
                "notebook_count": baseline_notebook_count - 1,
            },
            get_hierarchy_calls=1,
        )

        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "native_scope_matrix_passed": True,
            "page_relationships_passed": True,
            "strict_time_passed": True,
            "pagination_passed": True,
            "closed_notebook_exclusion_passed": True,
            "requests_recorded": len(evidence["requests"]),
            "closed_role": "query-b",
            "filesystem_deleted": False,
        }
        write_json(out / "result.json", result)
        return result

    async def execute(self, *args, **kwargs):
        raise RunnerFailure("Typed Query scenario requires lifecycle-controlled execution.")


__all__ = ["QueryScenario"]
