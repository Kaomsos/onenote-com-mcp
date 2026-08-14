from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tests.manual_validation.runner import main
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY


def test_query_metadata_scope_recipe_has_two_complete_fresh_roles() -> None:
    scenario = SCENARIO_REGISTRY.get("query-metadata-scopes")
    recipe = scenario.fixture_recipe
    spec = scenario.spec

    assert scenario.included_in_all is False
    assert scenario.requires_lifecycle_wrappers is True
    assert recipe.supports_cache is False
    assert tuple(role.role for role in recipe.cache_identity.notebook_roles) == (
        "query-b",
        "source",
    )
    assert recipe.manifest_keys_for_role("source") == frozenset(
        {
            "query_outer_group",
            "query_inner_group",
            "query_deep_section",
            "query_root_section",
            "query_parent_page",
            "query_child_page",
            "query_sibling_page",
            "query_root_page",
        }
    )
    assert recipe.manifest_keys_for_role("query-b") == frozenset(
        {
            "query_b_outer_group",
            "query_b_inner_group",
            "query_b_deep_section",
            "query_b_root_section",
            "query_b_parent_page",
            "query_b_child_page",
            "query_b_root_page",
        }
    )
    assert {
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
    } <= spec.tool_allowlist
    assert spec.policy.writes_enabled is True
    assert spec.policy.deletes_enabled is False
    assert spec.policy.permanent_deletes_enabled is False
    assert spec.policy.experimental_reparent_enabled is False
    assert spec.policy.experimental_copy_enabled is False
    assert spec.policy.move_page_enabled is False
    assert spec.policy.move_containers_enabled is False
    assert spec.policy.raw_xml_enabled is False


def test_query_metadata_scope_dry_run_is_human_gated_and_least_privilege(capsys) -> None:
    assert main(["query-metadata-scopes", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["agent_execution_prohibited"] is True
    assert payload["server_started"] is False
    assert payload["expected_mcp_process_starts"] == 1
    assert payload["cache"]["cache_access_performed"] is False
    assert payload["scenario_spec"]["execution_contract"] == {
        "fresh_only": True,
        "included_in_all": False,
        "lifecycle_close_probe_role": "query-b",
        "pagination": {"consistency": "live_hierarchy", "page_size": 2},
        "query_kind": "hierarchy_metadata",
    }
    assert payload["scenario_spec"]["mutation_policy"] == {
        "writes_enabled": True,
        "deletes_enabled": False,
        "permanent_deletes_enabled": False,
        "experimental_reparent_enabled": False,
        "experimental_reorder_section_enabled": False,
        "experimental_reorder_section_group_enabled": False,
        "experimental_copy_enabled": False,
        "move_page_enabled": False,
        "move_containers_enabled": False,
        "raw_xml_enabled": False,
    }


def _runtime_manifest() -> dict:
    source = {
        "id": "ns",
        "resource_type": "notebook",
        "name": "__query-metadata-scopes-source-2026-08-13-00-00-00__",
        "path": "Source",
        "parent_id": None,
    }
    query_b = {
        "id": "nb",
        "resource_type": "notebook",
        "name": "__query-metadata-scopes-query-b-2026-08-13-00-00-00__",
        "path": "QueryB",
        "parent_id": None,
    }

    def group(key, object_id, name, path, parent_id, notebook_id):
        return key, {
            "id": object_id,
            "resource_type": "section_group",
            "name": name,
            "path": path,
            "parent_id": parent_id,
            "notebook_id": notebook_id,
        }

    def section(key, object_id, name, path, parent_id, notebook_id):
        return key, {
            "id": object_id,
            "resource_type": "section",
            "name": name,
            "path": path,
            "parent_id": parent_id,
            "notebook_id": notebook_id,
        }

    def page(key, object_id, title, path, section_id, notebook_id, level=1, parent=None):
        return key, {
            "id": object_id,
            "resource_type": "page",
            "title": title,
            "path": path,
            "parent_id": section_id,
            "section_id": section_id,
            "notebook_id": notebook_id,
            "page_level": level,
            "parent_page_id": parent,
            "modified": "2026-08-13T00:00:00Z",
        }

    token = "Q-run-token-"
    structure = dict(
        [
            group("query_outer_group", "go", token + "Outer", "Source/Outer", "ns", "ns"),
            group("query_inner_group", "gi", token + "Inner", "Source/Outer/Inner", "go", "ns"),
            section("query_deep_section", "sd", token + "Deep", "Source/Outer/Inner/Deep", "gi", "ns"),
            section("query_root_section", "sr", token + "Root", "Source/Root", "ns", "ns"),
            page("query_parent_page", "pp", token + "Parent", "Source/Outer/Inner/Deep/Parent", "sd", "ns"),
            page("query_child_page", "pc", token + "Child", "Source/Outer/Inner/Deep/Child", "sd", "ns", 2, "pp"),
            page("query_sibling_page", "ps", token + "Sibling", "Source/Outer/Inner/Deep/Sibling", "sd", "ns"),
            page("query_root_page", "pr", token + "RootPage", "Source/Root/RootPage", "sr", "ns"),
            group("query_b_outer_group", "bgo", token + "BOuter", "QueryB/BOuter", "nb", "nb"),
            group("query_b_inner_group", "bgi", token + "BInner", "QueryB/BOuter/BInner", "bgo", "nb"),
            section("query_b_deep_section", "bsd", token + "BDeep", "QueryB/BOuter/BInner/BDeep", "bgi", "nb"),
            section("query_b_root_section", "bsr", token + "BRoot", "QueryB/BRoot", "nb", "nb"),
            page("query_b_parent_page", "bpp", token + "BParent", "QueryB/BOuter/BInner/BDeep/BParent", "bsd", "nb"),
            page("query_b_child_page", "bpc", token + "BChild", "QueryB/BOuter/BInner/BDeep/BChild", "bsd", "nb", 2, "bpp"),
            page("query_b_root_page", "bpr", token + "BRootPage", "QueryB/BRoot/BRootPage", "bsr", "nb"),
        ]
    )
    return {
        "notebook": source,
        "notebooks": {"source": source, "query-b": query_b},
        "structure": structure,
    }


class _FakeQueryClient:
    def __init__(self, run_dir: Path, manifest: dict) -> None:
        self.run_dir = run_dir
        self.manifest = manifest
        self.open_count = 2
        self.catalog = {
            item["id"]: item
            for item in [*manifest["notebooks"].values(), *manifest["structure"].values()]
        }

    def _tree(self, notebook_id: str) -> dict:
        notebook = self.catalog[notebook_id]
        children = [
            {"item": item, "children": []}
            for item in self.manifest["structure"].values()
            if item.get("notebook_id") == notebook_id
        ]
        return {"item": notebook, "children": children}

    def _append_audit(self, count: int) -> None:
        path = self.run_dir / "bridge-calls.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            for _ in range(count):
                stream.write(json.dumps({"operation": "get_hierarchy"}) + "\n")

    async def call_tool(self, name, arguments, retry_read=False):
        if name == "get_tree":
            return {"tree": self._tree(str(arguments["root_id"]))}

        scope = arguments.get("scope", {"mode": "root"})
        self._append_audit(1 if scope["mode"] == "root" else 2)
        structure = self.manifest["structure"]
        source_pages = ["pp", "pc", "ps", "pr"]
        deep_pages = ["pp", "pc", "ps"]
        all_pages = ["pp", "pc", "ps", "pr", "bpp", "bpc", "bpr"]
        if name == "query_notebook":
            ids = [] if "name_equals" in arguments else ["ns", "nb"]
            resource_type = "notebook"
        elif name == "query_section_group":
            resource_type = "section_group"
            if scope["mode"] == "root":
                ids = ["go", "gi", "bgo", "bgi"]
            elif scope["start_node_id"] == "ns":
                ids = ["go", "gi"]
            else:
                ids = ["gi"]
        elif name == "query_section":
            resource_type = "section"
            ids = ["sd", "sr", "bsd", "bsr"] if scope["mode"] == "root" else ["sd"]
        else:
            resource_type = "page"
            if scope["mode"] == "root" and "title_equals" in arguments:
                ids = []
            elif scope["mode"] == "root":
                ids = all_pages
            elif scope["start_node_id"] == "ns":
                ids = source_pages
            elif scope["start_node_id"] == "go":
                ids = deep_pages
            elif "parent_page_id" in arguments or "title_equals" in arguments:
                ids = ["pc"]
            else:
                ids = deep_pages

        total = len(ids)
        offset = int(arguments.get("offset", 0))
        page_size = int(arguments.get("page_size", 200))
        page_ids = ids[offset : offset + page_size]
        has_more = offset + len(page_ids) < total
        if scope["mode"] == "root":
            response_scope = {"mode": "root", "notebook_count": self.open_count}
        else:
            start_id = str(scope["start_node_id"])
            start = self.catalog[start_id]
            response_scope = {
                "mode": "start_node",
                "resource_type": start["resource_type"],
                "id": start_id,
                "path": start["path"],
                "notebook_id": start_id if start["resource_type"] == "notebook" else start["notebook_id"],
            }
        return {
            "items": [{"id": object_id} for object_id in page_ids],
            "count": len(page_ids),
            "total_matches": total,
            "offset": offset,
            "page_size": page_size,
            "has_more": has_more,
            "next_offset": offset + len(page_ids) if has_more else None,
            "pagination_consistency": "live_hierarchy",
            "resource_type": resource_type,
            "query_kind": "hierarchy_metadata",
            "scope": response_scope,
        }


class _FakeCloseWrapper:
    def __init__(self, client: _FakeQueryClient) -> None:
        self.client = client
        self.closed = False

    def close_exact_notebook(self):
        self.closed = True
        self.client.open_count = 1
        with (self.client.run_dir / "bridge-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": "close_notebook"}) + "\n")
        return {"closed": True, "source_notebook_id": "nb"}


def test_query_metadata_runtime_records_independent_expected_and_exact_bridge_calls(tmp_path) -> None:
    import asyncio

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "query-metadata-scopes").mkdir(parents=True)
    manifest = _runtime_manifest()
    client = _FakeQueryClient(run_dir, manifest)
    wrapper = _FakeCloseWrapper(client)
    scenario = SCENARIO_REGISTRY.get("query-metadata-scopes")

    result = asyncio.run(
        scenario.execute_with_lifecycle(
            SimpleNamespace(),
            RuntimeOptions(run_dir, 300, True, False),
            manifest,
            client=client,
            fixture_result={"status": "prepared"},
            wrappers={"query-b": wrapper},
        )
    )

    evidence_dir = run_dir / "scenarios" / "query-metadata-scopes"
    requests = json.loads((evidence_dir / "requests-and-responses.json").read_text(encoding="utf-8"))
    expected = json.loads((evidence_dir / "expected-results.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["requests_recorded"] == 18
    assert wrapper.closed is True
    assert len(requests["requests"]) == 18
    assert all(
        set(record["bridge_operations"]) == {"get_hierarchy"}
        for record in requests["requests"]
    )
    assert set(expected["items"]) == set(manifest["structure"])
    assert (evidence_dir / "typed-hierarchy-source.json").exists()
    assert (evidence_dir / "typed-hierarchy-query-b.json").exists()
