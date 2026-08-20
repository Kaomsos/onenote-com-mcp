"""Cache-capable two-Notebook fixture for typed hierarchy metadata Query validation."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from typing import Any

from ...path_budget import preflight_paths
from ...runtime import InvariantFailure, PathBudgetFailure
from ...test_utils import stable_item, utc_now, write_json
from ..common.fixture_builders import (
    enforce_page_position_with_query as enforce_page_position,
    ensure_group_with_query as ensure_group,
    ensure_page_with_query as ensure_page,
    ensure_section_with_query as ensure_section,
)


def compact_query_token(token: str) -> str:
    """Keep physical hierarchy names unique without repeating a full UUID per level."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _preflight_query_fixture_paths(
    context: FixtureContext,
    *,
    outer_name: str,
    inner_name: str,
    deep_name: str,
    deep_sibling_name: str,
    root_name: str,
    root_sibling_name: str,
) -> None:
    notebook_path = Path(context.notebook_path)
    targets = (
        (
            notebook_path / outer_name / inner_name / f"{deep_name}.one",
            "opaque_query_deep_section",
            f"{outer_name}/{inner_name}/{deep_name}.one",
        ),
        (
            notebook_path
            / outer_name
            / inner_name
            / f"{deep_sibling_name}.one",
            "opaque_query_deep_sibling_section",
            f"{outer_name}/{inner_name}/{deep_sibling_name}.one",
        ),
        (
            notebook_path / f"{root_name}.one",
            "opaque_query_root_section",
            f"{root_name}.one",
        ),
        (
            notebook_path / f"{root_sibling_name}.one",
            "opaque_query_root_sibling_section",
            f"{root_sibling_name}.one",
        ),
    )
    evidence_path = (
        context.options.run_dir / f"fixture-path-budget-{context.role}.json"
    )
    try:
        evidence = preflight_paths(
            targets,
            phase="typed_query_fixture_path_preflight",
        )
    except PathBudgetFailure as exc:
        exc.filesystem_changes_started = True
        exc.onenote_opened = True
        exc.mutation_started = context.role != "query-b"
        failure = exc.as_error_dict()
        failure.update(
            scenario="query",
            role=context.role,
            physical_names_use_compact_token=True,
        )
        write_json(evidence_path, failure)
        raise InvariantFailure(
            "Typed Query fixture physical path exceeds the managed 240-unit budget "
            f"before role mutation: {exc.actual_utf16}/{exc.limit_utf16}."
        ) from exc
    evidence.update(
        scenario="query",
        role=context.role,
        physical_names_use_compact_token=True,
    )
    write_json(evidence_path, evidence)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NESTED_SECTION_CACHE_UNSAFE_REASON,
    NotebookRoleSpec,
    RecipeBase,
)


class QueryFixtureRecipe(RecipeBase):
    recipe_version = 6
    supports_cache = False
    fresh_only_reason = NESTED_SECTION_CACHE_UNSAFE_REASON
    bundle_invariants = (
        "source and query-b Notebook IDs and paths are unique",
        "both working Notebook roles remain open until the explicit close probe",
        "all query target names contain the run-unique fixture token",
    )

    def __init__(self) -> None:
        profile = get_scenario_spec("query").fixture
        source_keys = (
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
        )
        query_b_keys = (
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
        )
        super().__init__(
            "query",
            notebook_roles=(
                NotebookRoleSpec(
                    "query-b",
                    replace(profile, name="typed-query-b", manifest_keys=query_b_keys),
                    {"manifest_keys": list(query_b_keys)},
                ),
                NotebookRoleSpec(
                    "source",
                    replace(profile, name="typed-query-source", manifest_keys=source_keys),
                    {"manifest_keys": list(source_keys)},
                ),
            ),
        )

    @staticmethod
    async def capture_snapshot(client, notebook_id: str) -> dict[str, Any]:
        """Capture Query fixture metadata without crossing into List/Expand tools."""

        items: list[dict[str, Any]] = []
        for tool in ("query_section_group", "query_section", "query_page"):
            offset = 0
            while True:
                response = await client.call_tool(
                    tool,
                    {
                        "scope": {
                            "mode": "start_node",
                            "start_node_id": notebook_id,
                        },
                        "offset": offset,
                        "page_size": 200,
                    },
                )
                page = response.get("items", [])
                if not isinstance(page, list) or any(
                    not isinstance(item, dict) for item in page
                ):
                    raise InvariantFailure(
                        f"{tool} returned invalid fixture snapshot items."
                    )
                items.extend(stable_item(item) for item in page)
                if not response.get("has_more"):
                    break
                next_offset = response.get("next_offset")
                if not isinstance(next_offset, int) or next_offset <= offset:
                    raise InvariantFailure(
                        f"{tool} fixture snapshot pagination did not advance."
                    )
                offset = next_offset
        ids = [str(item.get("id", "")) for item in items]
        if any(not object_id for object_id in ids) or len(ids) != len(set(ids)):
            raise InvariantFailure(
                "Query fixture snapshot contains a missing or duplicate object ID."
            )
        return {
            "captured_at": utc_now(),
            "notebook_id": notebook_id,
            "items": items,
            "page_hashes": {},
            "page_canonical_hashes": {},
            "page_reparent_hashes": {},
            "page_xml_hashes": {},
            "page_objects": {},
            "page_capability_projections": {},
            "page_mathml_structure_projections": {},
            "metadata_source": "typed_query_tools",
        }

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        token = compact_query_token(context.token)
        recorder = context.recorder
        role_suffix = "B" if context.role == "query-b" else ""
        outer_name = f"Q-{token}-{role_suffix}Outer"
        inner_name = f"Q-{token}-{role_suffix}Inner"
        deep_name = f"Q-{token}-{role_suffix}Deep"
        root_name = f"Q-{token}-{role_suffix}Root"
        deep_sibling_name = f"Q-{token}-{role_suffix}DeepSibling"
        root_sibling_name = f"Q-{token}-{role_suffix}RootSibling"
        _preflight_query_fixture_paths(
            context,
            outer_name=outer_name,
            inner_name=inner_name,
            deep_name=deep_name,
            deep_sibling_name=deep_sibling_name,
            root_name=root_name,
            root_sibling_name=root_sibling_name,
        )
        if context.role == "source":
            outer = recorder.record_structure(
                "query_outer_group",
                await ensure_group(context.client, context.notebook_id, outer_name),
            )
            recorder.record_structure(
                "query_outer_group_sibling",
                await ensure_group(
                    context.client,
                    context.notebook_id,
                    f"Q-{token}-OuterSibling",
                ),
            )
            inner = recorder.record_structure(
                "query_inner_group",
                await ensure_group(context.client, str(outer["id"]), inner_name),
            )
            recorder.record_structure(
                "query_inner_group_sibling",
                await ensure_group(
                    context.client,
                    str(outer["id"]),
                    f"Q-{token}-InnerSibling",
                ),
            )
            deep = recorder.record_structure(
                "query_deep_section",
                await ensure_section(context.client, str(inner["id"]), deep_name),
            )
            recorder.record_structure(
                "query_deep_section_sibling",
                await ensure_section(
                    context.client,
                    str(inner["id"]),
                    deep_sibling_name,
                ),
            )
            root = recorder.record_structure(
                "query_root_section",
                await ensure_section(context.client, context.notebook_id, root_name),
            )
            root_sibling = recorder.record_structure(
                "query_root_section_sibling",
                await ensure_section(
                    context.client, context.notebook_id, root_sibling_name
                ),
            )
            parent = recorder.record_structure(
                "query_parent_page",
                await ensure_page(context.client, str(deep["id"]), f"Q-{token}-Parent", "metadata query parent"),
            )
            child = await ensure_page(
                context.client, str(deep["id"]), f"Q-{token}-Child", "metadata query child"
            )
            child = await enforce_page_position(
                context.client,
                str(deep["id"]),
                str(child["id"]),
                str(parent["id"]),
                2,
            )
            recorder.record_structure("query_child_page", child)
            child_sibling = await ensure_page(
                context.client,
                str(deep["id"]),
                f"Q-{token}-ChildSibling",
                "metadata query child sibling",
            )
            child_sibling = await enforce_page_position(
                context.client,
                str(deep["id"]),
                str(child_sibling["id"]),
                str(child["id"]),
                2,
            )
            recorder.record_structure("query_child_page_sibling", child_sibling)
            recorder.record_structure(
                "query_sibling_page",
                await ensure_page(context.client, str(deep["id"]), f"Q-{token}-Sibling", "metadata query sibling"),
            )
            recorder.record_structure(
                "query_root_page",
                await ensure_page(context.client, str(root["id"]), f"Q-{token}-RootPage", "metadata query root"),
            )
            recorder.record_structure(
                "query_root_page_sibling",
                await ensure_page(
                    context.client,
                    str(root_sibling["id"]),
                    f"Q-{token}-RootPageSibling",
                    "metadata query root sibling",
                ),
            )
        elif context.role == "query-b":
            outer = recorder.record_structure(
                "query_b_outer_group",
                await ensure_group(context.client, context.notebook_id, outer_name),
            )
            recorder.record_structure(
                "query_b_outer_group_sibling",
                await ensure_group(
                    context.client,
                    context.notebook_id,
                    f"Q-{token}-BOuterSibling",
                ),
            )
            inner = recorder.record_structure(
                "query_b_inner_group",
                await ensure_group(context.client, str(outer["id"]), inner_name),
            )
            recorder.record_structure(
                "query_b_inner_group_sibling",
                await ensure_group(
                    context.client,
                    str(outer["id"]),
                    f"Q-{token}-BInnerSibling",
                ),
            )
            deep = recorder.record_structure(
                "query_b_deep_section",
                await ensure_section(context.client, str(inner["id"]), deep_name),
            )
            recorder.record_structure(
                "query_b_deep_section_sibling",
                await ensure_section(
                    context.client,
                    str(inner["id"]),
                    deep_sibling_name,
                ),
            )
            root = recorder.record_structure(
                "query_b_root_section",
                await ensure_section(context.client, context.notebook_id, root_name),
            )
            root_sibling = recorder.record_structure(
                "query_b_root_section_sibling",
                await ensure_section(
                    context.client, context.notebook_id, root_sibling_name
                ),
            )
            parent = recorder.record_structure(
                "query_b_parent_page",
                await ensure_page(
                    context.client,
                    str(deep["id"]),
                    f"Q-{token}-BParent",
                    "metadata query b parent",
                ),
            )
            child = await ensure_page(
                context.client,
                str(deep["id"]),
                f"Q-{token}-BChild",
                "metadata query b child",
            )
            child = await enforce_page_position(
                context.client,
                str(deep["id"]),
                str(child["id"]),
                str(parent["id"]),
                2,
            )
            recorder.record_structure("query_b_child_page", child)
            child_sibling = await ensure_page(
                context.client,
                str(deep["id"]),
                f"Q-{token}-BChildSibling",
                "metadata query b child sibling",
            )
            child_sibling = await enforce_page_position(
                context.client,
                str(deep["id"]),
                str(child_sibling["id"]),
                str(child["id"]),
                2,
            )
            recorder.record_structure(
                "query_b_child_page_sibling", child_sibling
            )
            recorder.record_structure(
                "query_b_sibling_page",
                await ensure_page(
                    context.client,
                    str(deep["id"]),
                    f"Q-{token}-BSibling",
                    "metadata query b sibling",
                ),
            )
            recorder.record_structure(
                "query_b_root_page",
                await ensure_page(
                    context.client,
                    str(root["id"]),
                    f"Q-{token}-BRootPage",
                    "metadata query b root",
                ),
            )
            recorder.record_structure(
                "query_b_root_page_sibling",
                await ensure_page(
                    context.client,
                    str(root_sibling["id"]),
                    f"Q-{token}-BRootPageSibling",
                    "metadata query b root sibling",
                ),
            )
        else:
            raise InvariantFailure(f"Unsupported typed Query fixture role: {context.role}")
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self, context: FixtureValidationContext, build: FixtureBuildResult
    ) -> tuple[str, ...]:
        expected_keys = self.manifest_keys_for_role(context.role, context.args)
        if set(build.structure) != set(expected_keys):
            raise InvariantFailure(
                f"Typed Query fixture role {context.role} received another role's structure."
            )
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        notebook_id = str(context.snapshot.get("notebook_id", ""))
        if context.role == "source":
            outer = resolved["query_outer_group"]
            outer_sibling = resolved["query_outer_group_sibling"]
            inner = resolved["query_inner_group"]
            inner_sibling = resolved["query_inner_group_sibling"]
            deep = resolved["query_deep_section"]
            deep_sibling = resolved["query_deep_section_sibling"]
            root = resolved["query_root_section"]
            root_sibling = resolved["query_root_section_sibling"]
            parent = resolved["query_parent_page"]
            child = resolved["query_child_page"]
            child_sibling = resolved["query_child_page_sibling"]
            sibling = resolved["query_sibling_page"]
            root_page = resolved["query_root_page"]
            root_page_sibling = resolved["query_root_page_sibling"]
            checks.require(
                outer.get("resource_type") == "section_group"
                and outer.get("parent_id") == notebook_id
                and outer_sibling.get("resource_type") == "section_group"
                and outer_sibling.get("parent_id") == notebook_id
                and inner.get("resource_type") == "section_group"
                and inner.get("parent_id") == outer.get("id")
                and inner_sibling.get("resource_type") == "section_group"
                and inner_sibling.get("parent_id") == outer.get("id")
                and deep.get("resource_type") == "section"
                and deep.get("parent_id") == inner.get("id")
                and deep_sibling.get("resource_type") == "section"
                and deep_sibling.get("parent_id") == inner.get("id")
                and root.get("resource_type") == "section"
                and root.get("parent_id") == notebook_id
                and root_sibling.get("resource_type") == "section"
                and root_sibling.get("parent_id") == notebook_id,
                "Typed Query nested container topology is invalid.",
                "source Notebook/SectionGroup/Section start-node chains are exact",
            )
            checks.require(
                all(
                    page.get("resource_type") == "page"
                    for page in (
                        parent,
                        child,
                        child_sibling,
                        sibling,
                        root_page,
                        root_page_sibling,
                    )
                )
                and parent.get("section_id") == deep.get("id")
                and parent.get("parent_page_id") in {None, ""}
                and int(parent.get("page_level", 0)) == 1
                and child.get("section_id") == deep.get("id")
                and child.get("parent_page_id") == parent.get("id")
                and int(child.get("page_level", 0)) == 2
                and child_sibling.get("section_id") == deep.get("id")
                and child_sibling.get("parent_page_id") == parent.get("id")
                and int(child_sibling.get("page_level", 0)) == 2
                and sibling.get("parent_page_id") in {None, ""}
                and sibling.get("section_id") == deep.get("id")
                and int(sibling.get("page_level", 0)) == 1
                and root_page.get("section_id") == root.get("id")
                and root_page.get("parent_page_id") in {None, ""}
                and int(root_page.get("page_level", 0)) == 1
                and root_page_sibling.get("section_id") == root_sibling.get("id")
                and root_page_sibling.get("parent_page_id") in {None, ""}
                and int(root_page_sibling.get("page_level", 0)) == 1,
                "Typed Query Page indentation topology is invalid.",
                "source Page Sections, root levels, and indentation parent are exact",
            )
            pages = (
                parent,
                child,
                child_sibling,
                sibling,
                root_page,
                root_page_sibling,
            )
        elif context.role == "query-b":
            outer = resolved["query_b_outer_group"]
            outer_sibling = resolved["query_b_outer_group_sibling"]
            inner = resolved["query_b_inner_group"]
            inner_sibling = resolved["query_b_inner_group_sibling"]
            deep = resolved["query_b_deep_section"]
            deep_sibling = resolved["query_b_deep_section_sibling"]
            root = resolved["query_b_root_section"]
            root_sibling = resolved["query_b_root_section_sibling"]
            parent = resolved["query_b_parent_page"]
            child = resolved["query_b_child_page"]
            child_sibling = resolved["query_b_child_page_sibling"]
            sibling = resolved["query_b_sibling_page"]
            root_page = resolved["query_b_root_page"]
            root_page_sibling = resolved["query_b_root_page_sibling"]
            checks.require(
                outer.get("resource_type") == "section_group"
                and outer.get("parent_id") == notebook_id
                and outer_sibling.get("resource_type") == "section_group"
                and outer_sibling.get("parent_id") == notebook_id
                and inner.get("resource_type") == "section_group"
                and inner.get("parent_id") == outer.get("id")
                and inner_sibling.get("resource_type") == "section_group"
                and inner_sibling.get("parent_id") == outer.get("id")
                and deep.get("resource_type") == "section"
                and deep.get("parent_id") == inner.get("id")
                and deep_sibling.get("resource_type") == "section"
                and deep_sibling.get("parent_id") == inner.get("id")
                and root.get("resource_type") == "section"
                and root.get("parent_id") == notebook_id
                and root_sibling.get("resource_type") == "section"
                and root_sibling.get("parent_id") == notebook_id,
                "Typed Query secondary Notebook topology is invalid.",
                "secondary Notebook has nested Groups plus direct Notebook/Group Sections",
            )
            checks.require(
                all(
                    page.get("resource_type") == "page"
                    for page in (
                        parent,
                        child,
                        child_sibling,
                        sibling,
                        root_page,
                        root_page_sibling,
                    )
                )
                and parent.get("section_id") == deep.get("id")
                and parent.get("parent_page_id") in {None, ""}
                and child.get("section_id") == deep.get("id")
                and child.get("parent_page_id") == parent.get("id")
                and child_sibling.get("section_id") == deep.get("id")
                and child_sibling.get("parent_page_id") == parent.get("id")
                and int(parent.get("page_level", 0)) == 1
                and int(child.get("page_level", 0)) == 2
                and int(child_sibling.get("page_level", 0)) == 2
                and sibling.get("section_id") == deep.get("id")
                and sibling.get("parent_page_id") in {None, ""}
                and int(sibling.get("page_level", 0)) == 1
                and root_page.get("section_id") == root.get("id")
                and root_page.get("parent_page_id") in {None, ""}
                and int(root_page.get("page_level", 0)) == 1
                and root_page_sibling.get("section_id") == root_sibling.get("id")
                and root_page_sibling.get("parent_page_id") in {None, ""}
                and int(root_page_sibling.get("page_level", 0)) == 1,
                "Typed Query secondary Notebook Page topology is invalid.",
                "secondary Notebook has root and indented Pages with exact Sections",
            )
            pages = (
                parent,
                child,
                child_sibling,
                sibling,
                root_page,
                root_page_sibling,
            )
        else:
            raise InvariantFailure(
                f"Unsupported typed Query validation role: {context.role}"
            )
        checks.require(
            all(str(page["id"]) in _by_id for page in pages),
            "Typed Query fixture snapshot lacks complete Page metadata evidence.",
            "every typed Query Page was observed through query_page",
        )
        return tuple(checks.checks)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source_title = str(
            observation.roles["source"].build.structure["query_parent_page"].get(
                "title", ""
            )
        )
        query_b_title = str(
            observation.roles["query-b"].build.structure[
                "query_b_parent_page"
            ].get("title", "")
        )
        source_suffix = "-Parent"
        query_b_suffix = "-BParent"
        if not (
            source_title.startswith("Q-")
            and source_title.endswith(source_suffix)
            and query_b_title.startswith("Q-")
            and query_b_title.endswith(query_b_suffix)
        ):
            raise InvariantFailure("Typed Query fixture titles lack the shared token shape.")
        source_token = source_title[2 : -len(source_suffix)]
        query_b_token = query_b_title[2 : -len(query_b_suffix)]
        if not source_token or source_token != query_b_token:
            raise InvariantFailure(
                "Typed Query fixture roles do not share one run-unique token."
            )
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + ("both typed Query roles share one non-empty run token",),
        )


RECIPE = QueryFixtureRecipe()

__all__ = ["QueryFixtureRecipe", "RECIPE", "compact_query_token"]
