"""Fresh two-Notebook fixture for typed hierarchy metadata Query validation."""

from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ..common.fixture_builders import (
    enforce_page_position,
    ensure_group,
    ensure_page,
    ensure_section,
)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .recipe_base import NotebookRoleSpec, RecipeBase


class QueryMetadataScopesFixtureRecipe(RecipeBase):
    recipe_version = 1
    supports_cache = False
    bundle_invariants = (
        "source and query-b Notebook IDs and paths are unique",
        "both fresh Notebook roles remain open until the explicit close probe",
        "all query target names contain the run-unique fixture token",
    )

    def __init__(self) -> None:
        profile = get_scenario_spec("query-metadata-scopes").fixture
        source_keys = (
            "query_outer_group",
            "query_inner_group",
            "query_deep_section",
            "query_root_section",
            "query_parent_page",
            "query_child_page",
            "query_sibling_page",
            "query_root_page",
        )
        query_b_keys = (
            "query_b_outer_group",
            "query_b_inner_group",
            "query_b_deep_section",
            "query_b_root_section",
            "query_b_parent_page",
            "query_b_child_page",
            "query_b_root_page",
        )
        super().__init__(
            "query-metadata-scopes",
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

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        token = context.token
        recorder = context.recorder
        if context.role == "source":
            outer = recorder.record_structure(
                "query_outer_group",
                await ensure_group(context.client, context.notebook_id, f"Q-{token}-Outer"),
            )
            inner = recorder.record_structure(
                "query_inner_group",
                await ensure_group(context.client, str(outer["id"]), f"Q-{token}-Inner"),
            )
            deep = recorder.record_structure(
                "query_deep_section",
                await ensure_section(context.client, str(inner["id"]), f"Q-{token}-Deep"),
            )
            root = recorder.record_structure(
                "query_root_section",
                await ensure_section(context.client, context.notebook_id, f"Q-{token}-Root"),
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
            recorder.record_structure(
                "query_sibling_page",
                await ensure_page(context.client, str(deep["id"]), f"Q-{token}-Sibling", "metadata query sibling"),
            )
            recorder.record_structure(
                "query_root_page",
                await ensure_page(context.client, str(root["id"]), f"Q-{token}-RootPage", "metadata query root"),
            )
        elif context.role == "query-b":
            outer = recorder.record_structure(
                "query_b_outer_group",
                await ensure_group(context.client, context.notebook_id, f"Q-{token}-BOuter"),
            )
            inner = recorder.record_structure(
                "query_b_inner_group",
                await ensure_group(context.client, str(outer["id"]), f"Q-{token}-BInner"),
            )
            deep = recorder.record_structure(
                "query_b_deep_section",
                await ensure_section(context.client, str(inner["id"]), f"Q-{token}-BDeep"),
            )
            root = recorder.record_structure(
                "query_b_root_section",
                await ensure_section(context.client, context.notebook_id, f"Q-{token}-BRoot"),
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
            recorder.record_structure(
                "query_b_root_page",
                await ensure_page(
                    context.client,
                    str(root["id"]),
                    f"Q-{token}-BRootPage",
                    "metadata query b root",
                ),
            )
        else:
            raise InvariantFailure(f"Unsupported typed Query fixture role: {context.role}")
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self, context: FixtureValidationContext, build: FixtureBuildResult
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        if "query_outer_group" in resolved:
            outer = resolved["query_outer_group"]
            inner = resolved["query_inner_group"]
            deep = resolved["query_deep_section"]
            parent = resolved["query_parent_page"]
            child = resolved["query_child_page"]
            sibling = resolved["query_sibling_page"]
            checks.require(
                inner.get("parent_id") == outer.get("id") and deep.get("parent_id") == inner.get("id"),
                "Typed Query nested container topology is invalid.",
                "Notebook/SectionGroup/Section start-node chain is exact",
            )
            checks.require(
                child.get("section_id") == deep.get("id")
                and child.get("parent_page_id") == parent.get("id")
                and int(child.get("page_level", 0)) == 2
                and sibling.get("parent_page_id") in {None, ""},
                "Typed Query Page indentation topology is invalid.",
                "Page direct Section and indentation parent are independently proven",
            )
        else:
            outer = resolved["query_b_outer_group"]
            inner = resolved["query_b_inner_group"]
            deep = resolved["query_b_deep_section"]
            root = resolved["query_b_root_section"]
            parent = resolved["query_b_parent_page"]
            child = resolved["query_b_child_page"]
            checks.require(
                outer.get("parent_id") == context.snapshot.get("notebook_id")
                and inner.get("parent_id") == outer.get("id")
                and deep.get("parent_id") == inner.get("id")
                and root.get("parent_id") == context.snapshot.get("notebook_id"),
                "Typed Query secondary Notebook topology is invalid.",
                "secondary Notebook has nested Groups plus direct Notebook/Group Sections",
            )
            checks.require(
                parent.get("section_id") == deep.get("id")
                and child.get("section_id") == deep.get("id")
                and child.get("parent_page_id") == parent.get("id")
                and int(parent.get("page_level", 0)) == 1
                and int(child.get("page_level", 0)) == 2
                and resolved["query_b_root_page"].get("section_id") == root.get("id"),
                "Typed Query secondary Notebook Page topology is invalid.",
                "secondary Notebook has root and indented Pages with exact Sections",
            )
        return tuple(checks.checks)


RECIPE = QueryMetadataScopesFixtureRecipe()

__all__ = ["QueryMetadataScopesFixtureRecipe", "RECIPE"]
