from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation.runner import main
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios.common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureRecorder,
)
from tests.manual_validation.scenarios.common.fixture_builders import (
    enforce_page_position_with_query,
    ensure_group_with_query,
    ensure_page_with_query,
    ensure_section_with_query,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
)
from tests.manual_validation.scenarios.fixture_recipes.query import (
    _preflight_query_fixture_paths,
    compact_query_token,
)


def test_query_metadata_scope_recipe_has_two_complete_fresh_only_roles() -> None:
    scenario = SCENARIO_REGISTRY.get("query")
    recipe = scenario.fixture_recipe
    spec = scenario.spec

    assert scenario.included_in_all is True
    assert scenario.requires_index_activation_checkpoint is False
    assert scenario.requires_lifecycle_wrappers is True
    assert recipe.recipe_version == 6
    assert recipe.supports_cache is False
    assert "Section below a SectionGroup" in recipe.fresh_only_reason
    assert tuple(role.role for role in recipe.cache_identity.notebook_roles) == (
        "query-b",
        "source",
    )
    assert recipe.manifest_keys_for_role("source") == frozenset(
        {
            "query_outer_group",
            "query_outer_group_sibling",
            "query_inner_group",
            "query_inner_group_sibling",
            "query_deep_section",
            "query_deep_section_sibling",
            "query_root_section",
            "query_root_section_sibling",
            "query_parent_page",
            "query_child_page",
            "query_child_page_sibling",
            "query_sibling_page",
            "query_root_page",
            "query_root_page_sibling",
        }
    )
    assert recipe.manifest_keys_for_role("query-b") == frozenset(
        {
            "query_b_outer_group",
            "query_b_outer_group_sibling",
            "query_b_inner_group",
            "query_b_inner_group_sibling",
            "query_b_deep_section",
            "query_b_deep_section_sibling",
            "query_b_root_section",
            "query_b_root_section_sibling",
            "query_b_parent_page",
            "query_b_child_page",
            "query_b_child_page_sibling",
            "query_b_sibling_page",
            "query_b_root_page",
            "query_b_root_page_sibling",
        }
    )
    assert {
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
    } <= spec.tool_allowlist
    assert not any(tool.startswith("list_") for tool in spec.tool_allowlist)
    assert not any(tool.startswith("expand_") for tool in spec.tool_allowlist)
    assert "get_page_xml" not in spec.tool_allowlist
    assert spec.policy.writes_enabled is True
    assert spec.policy.deletes_enabled is False
    assert spec.policy.organize_enabled is False
    assert spec.policy.create_enabled is True
    assert spec.policy.local_file_io_enabled is False
    assert spec.policy.ui_control_enabled is False
    assert spec.policy.notebook_lifecycle_enabled is False


def test_query_physical_token_is_compact_deterministic_and_not_the_uuid() -> None:
    source = "0a829c2d-bb96-4a89-af22-76d4328073c2"

    first = compact_query_token(source)
    second = compact_query_token(source)

    assert first == second
    assert len(first) == 16
    assert set(first) <= set("0123456789abcdef")
    assert source not in first


def test_query_deep_physical_path_is_budgeted_before_role_mutation(tmp_path) -> None:
    context = SimpleNamespace(
        notebook_path=Path("C:/") / ("n" * 220),
        options=SimpleNamespace(run_dir=tmp_path),
        role="query-b",
    )

    with pytest.raises(InvariantFailure, match="before role mutation"):
        _preflight_query_fixture_paths(
            context,
            outer_name="Q-short-BOuter",
            inner_name="Q-short-BInner",
            deep_name="Q-short-BDeep",
            deep_sibling_name="Q-short-BDeepSibling",
            root_name="Q-short-BRoot",
            root_sibling_name="Q-short-BRootSibling",
        )

    evidence = json.loads(
        (tmp_path / "fixture-path-budget-query-b.json").read_text(encoding="utf-8")
    )
    assert evidence["error_type"] == "path_budget_exceeded"
    assert evidence["mutation_started"] is False


def test_query_metadata_scope_dry_run_is_human_gated_and_least_privilege(capsys) -> None:
    assert main(["query", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["agent_execution_prohibited"] is True
    assert payload["server_started"] is False
    assert payload["expected_mcp_process_starts"] == 1
    assert payload["cache"]["cache_access_performed"] is False
    assert payload["scenario_spec"]["execution_contract"] == {
        "cache_supported": True,
        "included_in_all": True,
        "lifecycle_close_probe_role": "query-b",
        "pagination": {"consistency": "live_hierarchy", "page_size": 2},
        "query_kind": "hierarchy_metadata",
    }
    assert payload["scenario_spec"]["mutation_policy"] == {
        "writes_enabled": True,
        "deletes_enabled": False,
        "organize_enabled": False,
            "create_enabled": True,
        "local_file_io_enabled": False,
        "ui_control_enabled": False,
        "notebook_lifecycle_enabled": False,
    }


def test_query_metadata_use_cache_dry_run_fails_fast(capsys) -> None:
    assert main(
        ["query", "--use-cache", "--dry-run", "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["cache"]["decision"] == "rejected_fresh_only"
    assert payload["cache"]["enabled"] is False
    assert payload["expected_mcp_process_starts"] == 0
    assert payload["ordered_steps"] == [
        {
            "step": "preflight-fresh-only-rejects-cache",
            "trust_boundary": "static fresh-only Recipe contract",
            "allowed_operations": [],
            "target": "reject before lifecycle, MCP, cache, or mutation",
            "reason": SCENARIO_REGISTRY.get("query").fixture_recipe.fresh_only_reason,
        }
    ]


def _runtime_manifest() -> dict:
    source = {
        "id": "ns",
        "resource_type": "notebook",
        "name": "__query-source-2026-08-13-00-00-00__",
        "path": "Source",
        "parent_id": None,
    }
    query_b = {
        "id": "nb",
        "resource_type": "notebook",
        "name": "__query-query-b-2026-08-13-00-00-00__",
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
            group("query_outer_group_sibling", "gos", token + "OuterSibling", "Source/OuterSibling", "ns", "ns"),
            group("query_inner_group", "gi", token + "Inner", "Source/Outer/Inner", "go", "ns"),
            group("query_inner_group_sibling", "gis", token + "InnerSibling", "Source/Outer/InnerSibling", "go", "ns"),
            section("query_deep_section", "sd", token + "Deep", "Source/Outer/Inner/Deep", "gi", "ns"),
            section("query_deep_section_sibling", "sds", token + "DeepSibling", "Source/Outer/Inner/DeepSibling", "gi", "ns"),
            section("query_root_section", "sr", token + "Root", "Source/Root", "ns", "ns"),
            section("query_root_section_sibling", "srs", token + "RootSibling", "Source/RootSibling", "ns", "ns"),
            page("query_parent_page", "pp", token + "Parent", "Source/Outer/Inner/Deep/Parent", "sd", "ns"),
            page("query_child_page", "pc", token + "Child", "Source/Outer/Inner/Deep/Child", "sd", "ns", 2, "pp"),
            page("query_child_page_sibling", "pcs", token + "ChildSibling", "Source/Outer/Inner/Deep/ChildSibling", "sd", "ns", 2, "pp"),
            page("query_sibling_page", "ps", token + "Sibling", "Source/Outer/Inner/Deep/Sibling", "sd", "ns"),
            page("query_root_page", "pr", token + "RootPage", "Source/Root/RootPage", "sr", "ns"),
            page("query_root_page_sibling", "prs", token + "RootPageSibling", "Source/RootSibling/RootPageSibling", "srs", "ns"),
            group("query_b_outer_group", "bgo", token + "BOuter", "QueryB/BOuter", "nb", "nb"),
            group("query_b_outer_group_sibling", "bgos", token + "BOuterSibling", "QueryB/BOuterSibling", "nb", "nb"),
            group("query_b_inner_group", "bgi", token + "BInner", "QueryB/BOuter/BInner", "bgo", "nb"),
            group("query_b_inner_group_sibling", "bgis", token + "BInnerSibling", "QueryB/BOuter/BInnerSibling", "bgo", "nb"),
            section("query_b_deep_section", "bsd", token + "BDeep", "QueryB/BOuter/BInner/BDeep", "bgi", "nb"),
            section("query_b_deep_section_sibling", "bsds", token + "BDeepSibling", "QueryB/BOuter/BInner/BDeepSibling", "bgi", "nb"),
            section("query_b_root_section", "bsr", token + "BRoot", "QueryB/BRoot", "nb", "nb"),
            section("query_b_root_section_sibling", "bsrs", token + "BRootSibling", "QueryB/BRootSibling", "nb", "nb"),
            page("query_b_parent_page", "bpp", token + "BParent", "QueryB/BOuter/BInner/BDeep/BParent", "bsd", "nb"),
            page("query_b_child_page", "bpc", token + "BChild", "QueryB/BOuter/BInner/BDeep/BChild", "bsd", "nb", 2, "bpp"),
            page("query_b_child_page_sibling", "bpcs", token + "BChildSibling", "QueryB/BOuter/BInner/BDeep/BChildSibling", "bsd", "nb", 2, "bpp"),
            page("query_b_sibling_page", "bps", token + "BSibling", "QueryB/BOuter/BInner/BDeep/BSibling", "bsd", "nb"),
            page("query_b_root_page", "bpr", token + "BRootPage", "QueryB/BRoot/BRootPage", "bsr", "nb"),
            page("query_b_root_page_sibling", "bprs", token + "BRootPageSibling", "QueryB/BRootSibling/BRootPageSibling", "bsrs", "nb"),
        ]
    )
    return {
        "notebook": source,
        "notebooks": {"source": source, "query-b": query_b},
        "structure": structure,
    }


def test_query_fixture_has_two_direct_items_at_every_exercised_hierarchy_level() -> None:
    manifest = _runtime_manifest()
    structure = manifest["structure"]

    def ids(*, resource_type, parent_id=None, section_id=None, parent_page_id=None):
        return {
            item["id"]
            for item in structure.values()
            if item["resource_type"] == resource_type
            and (parent_id is None or item.get("parent_id") == parent_id)
            and (section_id is None or item.get("section_id") == section_id)
            and (
                parent_page_id is None
                or item.get("parent_page_id") == parent_page_id
            )
        }

    for role in ("source", "query-b"):
        suffix = "" if role == "source" else "_b"
        notebook_id = manifest["notebooks"][role]["id"]
        outer_id = structure[f"query{suffix}_outer_group"]["id"]
        inner_id = structure[f"query{suffix}_inner_group"]["id"]
        deep_id = structure[f"query{suffix}_deep_section"]["id"]
        parent_id = structure[f"query{suffix}_parent_page"]["id"]

        assert len(ids(resource_type="section_group", parent_id=notebook_id)) == 2
        assert len(ids(resource_type="section_group", parent_id=outer_id)) == 2
        assert len(ids(resource_type="section", parent_id=notebook_id)) == 2
        assert len(ids(resource_type="section", parent_id=inner_id)) == 2
        assert len(
            {
                item["id"]
                for item in structure.values()
                if item["resource_type"] == "page"
                and item.get("section_id") == deep_id
                and item.get("parent_page_id") is None
            }
        ) == 2
        assert len(
            ids(
                resource_type="page",
                section_id=deep_id,
                parent_page_id=parent_id,
            )
        ) == 2


def test_query_fixture_helpers_only_read_through_typed_query() -> None:
    import asyncio

    class QueryOnlyClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, name, arguments, retry_read=False):
            self.calls.append(name)
            if name == "query_section_group":
                return {"items": [{"id": "group", "name": "Group"}]}
            if name == "query_section":
                return {"items": [{"id": "section", "name": "Section"}]}
            if name == "query_page" and "title_equals" in arguments:
                return {"items": [{"id": "page", "title": "Page"}]}
            if name == "query_page":
                return {
                    "items": [
                        {
                            "id": "page",
                            "title": "Page",
                            "order": 0,
                            "page_level": 1,
                        }
                    ],
                    "has_more": False,
                }
            raise AssertionError(f"Unexpected tool: {name}")

    async def exercise(client: QueryOnlyClient) -> None:
        await ensure_group_with_query(client, "notebook", "Group")
        await ensure_section_with_query(client, "group", "Section")
        await ensure_page_with_query(client, "section", "Page", "body")
        await enforce_page_position_with_query(client, "section", "page", "", 1)

    client = QueryOnlyClient()
    asyncio.run(exercise(client))

    assert client.calls == [
        "query_section_group",
        "query_section",
        "query_page",
        "query_page",
    ]


def test_query_recipe_places_second_direct_child_after_first_child(
    monkeypatch,
    tmp_path,
) -> None:
    import asyncio
    from tests.manual_validation.scenarios.fixture_recipes import (
        query as recipe_module,
    )

    page_titles: dict[str, str] = {}
    requests: list[tuple[str, str, int]] = []

    def item(resource_type: str, parent_id: str, name: str) -> dict:
        object_id = f"{resource_type}:{name}"
        value = {
            "id": object_id,
            "resource_type": resource_type,
            "name": name,
            "parent_id": parent_id,
        }
        if resource_type == "page":
            value.update(
                title=name,
                section_id=parent_id,
                page_level=1,
                parent_page_id=None,
            )
            page_titles[object_id] = name
        return value

    async def ensure_group(_client, parent_id, name):
        return item("section_group", parent_id, name)

    async def ensure_section(_client, parent_id, name):
        return item("section", parent_id, name)

    async def ensure_page(_client, section_id, title, _content):
        return item("page", section_id, title)

    async def enforce(_client, section_id, page_id, after_page_id, page_level):
        requests.append((page_id, after_page_id, page_level))
        return {
            **item("page", section_id, page_titles[page_id]),
            "id": page_id,
            "page_level": page_level,
        }

    monkeypatch.setattr(recipe_module, "ensure_group", ensure_group)
    monkeypatch.setattr(recipe_module, "ensure_section", ensure_section)
    monkeypatch.setattr(recipe_module, "ensure_page", ensure_page)
    monkeypatch.setattr(recipe_module, "enforce_page_position", enforce)

    recipe = SCENARIO_REGISTRY.get("query").fixture_recipe
    spec = SCENARIO_REGISTRY.get("query").spec
    for role in ("source", "query-b"):
        run_dir = tmp_path / role
        run_dir.mkdir()
        notebook = {"id": f"notebook-{role}", "name": role}
        notebook_path = str(run_dir / "notebook")
        recorder = FixtureRecorder(
            run_dir=run_dir,
            notebook=notebook,
            notebook_path=notebook_path,
            spec=spec,
            allowed_keys=recipe.manifest_keys_for_role(role),
            role=role,
        )
        asyncio.run(
            recipe.build(
                FixtureContext(
                    args=argparse.Namespace(),
                    options=RuntimeOptions(run_dir, 300, False, False),
                    client=object(),
                    notebook=notebook,
                    notebook_path=notebook_path,
                    spec=spec,
                    token="query-token",
                    recorder=recorder,
                    role=role,
                )
            )
        )

    token = compact_query_token("query-token")
    by_page_title = {
        page_titles[page_id]: (page_titles[after_page_id], level)
        for page_id, after_page_id, level in requests
    }
    assert by_page_title[f"Q-{token}-Child"] == (
        f"Q-{token}-Parent",
        2,
    )
    assert by_page_title[f"Q-{token}-ChildSibling"] == (
        f"Q-{token}-Child",
        2,
    )
    assert by_page_title[f"Q-{token}-BChild"] == (
        f"Q-{token}-BParent",
        2,
    )
    assert by_page_title[f"Q-{token}-BChildSibling"] == (
        f"Q-{token}-BChild",
        2,
    )


def _query_fixture_observation() -> FixtureBundleObservation:
    scenario = SCENARIO_REGISTRY.get("query")
    recipe = scenario.fixture_recipe
    manifest = _runtime_manifest()
    roles = {}
    for role in ("query-b", "source"):
        structure = {
            key: deepcopy(manifest["structure"][key])
            for key in recipe.manifest_keys_for_role(role)
        }
        page_ids = {
            str(item["id"])
            for item in structure.values()
            if item["resource_type"] == "page"
        }
        roles[role] = FixtureRoleObservation(
            role=role,
            args=argparse.Namespace(),
            notebook=manifest["notebooks"][role],
            notebook_path=f"C:/run/{role}",
            snapshot={
                "notebook_id": manifest["notebooks"][role]["id"],
                "items": list(structure.values()),
                "page_hashes": {page_id: f"hash-{page_id}" for page_id in page_ids},
            },
            build=FixtureBuildResult(structure, {}),
        )
    return FixtureBundleObservation(roles=roles)


def test_query_recipe_uses_role_aware_complete_bundle_validation() -> None:
    recipe = SCENARIO_REGISTRY.get("query").fixture_recipe
    observation = _query_fixture_observation()

    report = recipe.validate_live(observation)

    assert report.passed is True
    assert "every typed Query Page was observed through query_page" in (
        report.role_checks["query-b"]
    )
    assert "both typed Query roles share one non-empty run token" in report.bundle_checks


def test_query_recipe_rejects_cross_role_token_drift() -> None:
    recipe = SCENARIO_REGISTRY.get("query").fixture_recipe
    observation = _query_fixture_observation()
    query_b = observation.roles["query-b"]
    structure = deepcopy(dict(query_b.build.structure))
    structure["query_b_parent_page"]["title"] = "Q-other-token-BParent"
    broken = FixtureBundleObservation(
        roles={
            **observation.roles,
            "query-b": FixtureRoleObservation(
                role=query_b.role,
                args=query_b.args,
                notebook=query_b.notebook,
                notebook_path=query_b.notebook_path,
                snapshot=query_b.snapshot,
                build=FixtureBuildResult(structure, {}),
            ),
        }
    )

    with pytest.raises(InvariantFailure, match="share one run-unique token"):
        recipe.validate_live(broken)


def test_query_recipe_snapshot_uses_query_tools_only() -> None:
    import asyncio

    manifest = _runtime_manifest()

    class SnapshotClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, name, arguments):
            self.calls.append(name)
            assert name in {
                "query_section_group",
                "query_section",
                "query_page",
            }
            resource_type = {
                "query_section_group": "section_group",
                "query_section": "section",
                "query_page": "page",
            }[name]
            notebook_id = arguments["scope"]["start_node_id"]
            items = [
                item
                for item in manifest["structure"].values()
                if item["resource_type"] == resource_type
                and item["notebook_id"] == notebook_id
            ]
            return {"items": items, "has_more": False, "next_offset": None}

    client = SnapshotClient()
    snapshot = asyncio.run(
        SCENARIO_REGISTRY.get("query").fixture_recipe.capture_snapshot(client, "ns")
    )

    assert set(client.calls) == {
        "query_section_group",
        "query_section",
        "query_page",
    }
    assert snapshot["metadata_source"] == "typed_query_tools"
    assert len(snapshot["items"]) == 14


class _FakeQueryClient:
    def __init__(self, run_dir: Path, manifest: dict) -> None:
        self.run_dir = run_dir
        self.manifest = manifest
        self.open_count = 4
        self.calls: list[str] = []
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
        self.calls.append(name)
        if not name.startswith("query_"):
            raise AssertionError(f"Query scenario crossed tool families: {name}")

        scope = arguments.get("scope", {"mode": "root"})
        self._append_audit(1 if scope["mode"] == "root" else 2)
        structure = self.manifest["structure"]
        source_pages = ["pp", "pc", "pcs", "ps", "pr", "prs"]
        deep_pages = ["pp", "pc", "pcs", "ps"]
        all_pages = [
            "pp", "pc", "pcs", "ps", "pr", "prs",
            "bpp", "bpc", "bpcs", "bps", "bpr", "bprs",
        ]
        if name == "query_notebook":
            if "name_equals" in arguments:
                ids = []
            elif "name_contains" in arguments:
                ids = ["ns", "nb"]
            else:
                ids = ["ns", "nb"] + [
                    f"unrelated-{index}"
                    for index in range(self.open_count - 2)
                ]
            resource_type = "notebook"
        elif name == "query_section_group":
            resource_type = "section_group"
            if scope["mode"] == "root":
                ids = ["go", "gos", "gi", "gis", "bgo", "bgos", "bgi", "bgis"]
            elif scope["start_node_id"] == "ns":
                ids = ["go", "gos", "gi", "gis"]
            else:
                ids = ["gi", "gis"]
        elif name == "query_section":
            resource_type = "section"
            ids = [
                "sd", "sds", "sr", "srs", "bsd", "bsds", "bsr", "bsrs"
            ] if scope["mode"] == "root" else ["sd", "sds"]
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
            elif "parent_page_id" in arguments:
                ids = ["pc", "pcs"]
            elif "title_equals" in arguments:
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
        self.client.open_count -= 1
        with (self.client.run_dir / "bridge-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": "close_notebook"}) + "\n")
        return {"closed": True, "source_notebook_id": "nb"}


class _CollisionQueryClient(_FakeQueryClient):
    async def call_tool(self, name, arguments, retry_read=False):
        result = await super().call_tool(name, arguments, retry_read=retry_read)
        if name == "query_notebook" and "name_contains" in arguments:
            result["items"].append({"id": "retained-working-copy"})
            result["count"] += 1
            result["total_matches"] += 1
        return result


def test_query_metadata_runtime_records_independent_expected_and_exact_bridge_calls(tmp_path) -> None:
    import asyncio

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "query").mkdir(parents=True)
    manifest = _runtime_manifest()
    client = _FakeQueryClient(run_dir, manifest)
    wrapper = _FakeCloseWrapper(client)
    scenario = SCENARIO_REGISTRY.get("query")

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

    evidence_dir = run_dir / "scenarios" / "query"
    requests = json.loads((evidence_dir / "requests-and-responses.json").read_text(encoding="utf-8"))
    expected = json.loads((evidence_dir / "expected-results.json").read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["requests_recorded"] == 20
    assert wrapper.closed is True
    assert len(requests["requests"]) == 20
    assert set(client.calls) <= {
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
    }
    assert all(
        set(record["bridge_operations"]) == {"get_hierarchy"}
        for record in requests["requests"]
    )
    assert set(expected["items"]) == set(manifest["structure"])
    assert (evidence_dir / "fixture-metadata-source.json").exists()
    assert (evidence_dir / "fixture-metadata-query-b.json").exists()
    baseline = json.loads(
        (evidence_dir / "open-notebook-baseline.json").read_text(encoding="utf-8")
    )
    assert baseline == {
        "schema_version": 1,
        "open_notebook_count": 4,
        "fixture_notebook_count": 2,
        "all_fixture_notebooks_present": True,
        "unrelated_notebook_identity_persisted": False,
    }
    assert all(
        request["response"]["scope"]["notebook_count"] == (
            3 if request["label"].startswith("closed-") else 4
        )
        for request in requests["requests"]
        if request["response"]["scope"]["mode"] == "root"
    )
    multi_item_query_labels = {
        "notebook-root",
        "groups-root",
        "groups-from-notebook",
        "groups-from-group",
        "sections-root",
        "sections-from-group",
        "pages-from-notebook",
        "pages-from-group",
        "pages-from-section",
        "page-indentation-parent",
    }
    multi_item_queries = {
        request["label"]: request for request in requests["requests"]
        if request["label"] in multi_item_query_labels
    }
    assert set(multi_item_queries) == multi_item_query_labels
    assert all(
        case["response"]["count"] >= 2 for case in multi_item_queries.values()
    )


def test_query_cache_warns_only_when_reused_token_produces_extra_hits(tmp_path) -> None:
    import asyncio

    run_dir = tmp_path / "run"
    (run_dir / "scenarios" / "query").mkdir(parents=True)
    manifest = _runtime_manifest()
    client = _CollisionQueryClient(run_dir, manifest)
    wrapper = _FakeCloseWrapper(client)
    scenario = SCENARIO_REGISTRY.get("query")

    with pytest.raises(InvariantFailure, match="cache_query_collision"):
        asyncio.run(
            scenario.execute_with_lifecycle(
                SimpleNamespace(),
                RuntimeOptions(run_dir, 300, True, False, use_cache=True),
                manifest,
                client=client,
                fixture_result={"status": "prepared"},
                wrappers={"query-b": wrapper},
            )
        )

    warning = json.loads(
        (
            run_dir
            / "scenarios"
            / "query"
            / "cache-query-collision-warning.json"
        ).read_text(encoding="utf-8")
    )
    assert warning["extra_hit_ids"] == ["retained-working-copy"]
    assert warning["query_text_persisted"] is False
    assert wrapper.closed is False
