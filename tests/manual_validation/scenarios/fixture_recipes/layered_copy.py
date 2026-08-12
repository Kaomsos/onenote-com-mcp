"""Parameterized rich Page component shared by Copy and strict Move recipes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..common.fixture_builders import (
    enforce_page_position,
    ensure_copy_list_tag_fixture,
    ensure_copy_rich_fixture,
    ensure_group,
    ensure_page,
    ensure_section,
)
from ..common.config import RELAXED_COPY_CAPABILITIES, ROOT_PAGE_COPY_CAPABILITIES
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import NotebookRoleSpec, RecipeBase, evidence


class LayeredFixtureKind(Enum):
    PAGE = "page"
    SECTION = "section"
    SECTION_GROUP = "section_group"
    NOTEBOOK = "notebook"
    MOVE = "move"


def _merge_automated_content(
    copy_fixture: dict,
    capabilities: tuple[str, ...],
) -> None:
    automated_content = list(copy_fixture.get("automated_content", ()))
    automated_content.extend(
        capability
        for capability in capabilities
        if capability not in automated_content
    )
    copy_fixture["automated_content"] = automated_content


@dataclass(frozen=True)
class LayeredFixtureConfig:
    kind: LayeredFixtureKind
    parent_title: str = "Rich-Page"
    semantic_title: str = "List-Tag-Page"
    include_equations: bool = False


class LayeredCopyFixtureRecipe(RecipeBase):
    # Version 2 makes the live Page XML capability projection part of the
    # fixture contract.  Version 1 could accept stale build-time List/Tag
    # evidence even when a materialized Page no longer exposed those nodes.
    recipe_version = 2

    def __init__(
        self,
        scenario_name: str,
        config: LayeredFixtureConfig,
        *,
        notebook_roles: tuple[NotebookRoleSpec, ...] | None = None,
    ) -> None:
        super().__init__(scenario_name, notebook_roles=notebook_roles)
        self.config = config

    async def _pages(self, context: FixtureContext, section: dict) -> tuple[dict, dict, dict]:
        r = context.recorder
        parent_key = "disposable_page" if self.config.kind is LayeredFixtureKind.MOVE else "parent_page"
        parent = await ensure_page(context.client, section["id"], self.config.parent_title, f"Copy token: {context.token}")
        semantic = await ensure_page(
            context.client,
            section["id"],
            self.config.semantic_title,
            f"Semantic copy token: {context.token}",
        )
        r.record_structure(parent_key, parent)
        r.record_structure("semantic_page", semantic)
        parent, copy_fixture = await ensure_copy_rich_fixture(
            context.client,
            parent,
            context.options.run_dir,
            include_equations=self.config.include_equations,
        )
        parent = await enforce_page_position(context.client, section["id"], parent["id"], "", 1)
        semantic = await enforce_page_position(context.client, section["id"], semantic["id"], parent["id"], 2)
        semantic, semantic_fixture = await ensure_copy_list_tag_fixture(context.client, semantic)
        _merge_automated_content(copy_fixture, ("list", "tag"))
        copy_fixture["semantic_page"] = semantic_fixture
        r.refresh_structure(parent_key, parent)
        r.refresh_structure("semantic_page", semantic)
        r.record_evidence("copy_fixture", copy_fixture)
        return parent, semantic, copy_fixture

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        kind = self.config.kind
        if kind is LayeredFixtureKind.PAGE:
            source = r.record_structure("source_section", await ensure_section(context.client, context.notebook_id, "Source"))
            r.record_structure("disposable_section", await ensure_section(context.client, context.notebook_id, "Destination"))
        elif kind is LayeredFixtureKind.SECTION:
            source_group = r.record_structure("group_a", await ensure_group(context.client, context.notebook_id, "Source-Group"))
            r.record_structure("group_b", await ensure_group(context.client, context.notebook_id, "Group-B"))
            source = r.record_structure("source_section", await ensure_section(context.client, source_group["id"], "Source-Section"))
        elif kind is LayeredFixtureKind.SECTION_GROUP:
            source_group = r.record_structure("group_a", await ensure_group(context.client, context.notebook_id, "Group-A"))
            source = r.record_structure("source_section", await ensure_section(context.client, source_group["id"], "Source-Section"))
        elif kind is LayeredFixtureKind.NOTEBOOK:
            source = r.record_structure("source_section", await ensure_section(context.client, context.notebook_id, "Source-Section"))
        elif kind is LayeredFixtureKind.MOVE:
            source = await ensure_section(context.client, context.notebook_id, "Source")
            destination = r.record_structure(
                "destination_section",
                await ensure_section(context.client, context.notebook_id, "Destination"),
            )
            r.record_structure(
                "collision_anchor",
                await ensure_page(
                    context.client,
                    destination["id"],
                    self.config.semantic_title,
                    f"Move collision anchor token: {context.token}",
                ),
            )
        else:  # pragma: no cover - closed enum
            raise AssertionError(kind)
        await self._pages(context, source)
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        kind = self.config.kind
        if kind is LayeredFixtureKind.PAGE:
            checks.require(resolved["parent_page"].get("section_id") == resolved["source_section"]["id"] and resolved["source_section"]["id"] != resolved["disposable_section"]["id"], "Copy Page fixture source and destination are not isolated Sections.", "Page Copy source and destination are isolated Sections")
        elif kind is LayeredFixtureKind.SECTION:
            checks.require(resolved["source_section"].get("parent_id") == resolved["group_a"]["id"] and resolved["group_a"]["id"] != resolved["group_b"]["id"], "Copy Section fixture source and destination groups are invalid.", "source Section and destination Group are distinct")
        elif kind is LayeredFixtureKind.SECTION_GROUP:
            checks.require(resolved["source_section"].get("parent_id") == resolved["group_a"]["id"], "Copy SectionGroup fixture source Section escaped its source Group.", "rich source Section is contained by the source Group")
        elif kind is LayeredFixtureKind.NOTEBOOK:
            checks.require(resolved["parent_page"].get("section_id") == resolved["source_section"]["id"], "Copy Notebook fixture rich Page escaped its source Section.", "rich source Page is contained by the source Notebook Section")
        elif kind is LayeredFixtureKind.MOVE:
            checks.require(resolved["disposable_page"].get("section_id") != resolved["destination_section"]["id"], "Move source Page already belongs to the destination Section.", "Move source Page and destination Section are distinct")
            checks.require(
                resolved["collision_anchor"].get("section_id")
                == resolved["destination_section"]["id"]
                and resolved["collision_anchor"].get("title")
                == resolved["semantic_page"].get("title"),
                "Move duplicate-title collision anchor is missing or outside Destination.",
                "Destination contains a same-title anchor with an independently captured body",
            )
        parent = resolved["disposable_page" if kind is LayeredFixtureKind.MOVE else "parent_page"]
        semantic = resolved["semantic_page"]
        checks.require(parent.get("section_id") == semantic.get("section_id") and int(parent.get("page_level", 0)) == 1 and int(semantic.get("page_level", 0)) == 2 and semantic.get("parent_page_id") == parent["id"], "Layered Copy fixture Page topology is invalid.", "strict parent and semantic child form an isolated two-page subtree")
        copy_fixture = evidence(build, "copy_fixture")
        automated = {str(value).casefold() for value in (copy_fixture or {}).get("automated_content", [])}
        checks.require({"rich_text", "table", "image"}.issubset(automated), "Rich Copy fixture is missing a required automated content capability.", "rich text, table, and image capabilities were created and observed")
        if self.config.include_equations:
            equation_evidence = (copy_fixture or {}).get("equations")
            checks.require(
                {"inline_equation", "display_equation"}.issubset(automated)
                and equation_evidence
                == {
                    "mathml_roots": 2,
                    "inline_equations": 1,
                    "display_equations": 1,
                    "namespace_declarations": 2,
                    "redundant_breaks_before_display": 0,
                    "standalone_display_oes": 1,
                    "nonempty_display_predecessors": 1,
                    "empty_oes_before_display": 0,
                },
                "Rich Copy fixture is missing its exact inline/display equation pair.",
                "rich parent contains one inline and one display MathML equation",
            )
        semantic_evidence = (copy_fixture or {}).get("semantic_page")
        checks.require(isinstance(semantic_evidence, dict) and {"List", "Tag"}.issubset(semantic_evidence.get("observed_capabilities", [])) and semantic_evidence.get("observed_counts", {}).get("List") == 3 and semantic_evidence.get("observed_counts", {}).get("Tag") == 3, "Semantic Copy fixture is missing the three generated List/Tag items.", "semantic child contains three generated mixed List/Tag items")
        projections = context.snapshot.get("page_capability_projections")
        parent_projection = (
            projections.get(parent["id"])
            if isinstance(projections, dict)
            else None
        )
        semantic_projection = (
            projections.get(semantic["id"])
            if isinstance(projections, dict)
            else None
        )

        def has_live_capabilities(projection: object, required: set[str]) -> bool:
            return (
                isinstance(projection, dict)
                and projection.get("complete") is True
                and required.issubset(
                    {str(value) for value in projection.get("capabilities", [])}
                )
            )

        checks.require(
            has_live_capabilities(
                parent_projection,
                (
                    {*ROOT_PAGE_COPY_CAPABILITIES, "DisplayEquation"}
                    if self.config.include_equations
                    else ROOT_PAGE_COPY_CAPABILITIES
                ),
            ),
            "Rich Copy fixture live Page XML is missing a required capability.",
            "rich parent live Page XML exposes the capabilities required by Copy planning",
        )
        checks.require(
            has_live_capabilities(semantic_projection, RELAXED_COPY_CAPABILITIES),
            "Semantic Copy fixture live Page XML is missing List/Tag capabilities.",
            "semantic child live Page XML exposes List/Tag to Copy planning",
        )
        return tuple(checks.checks)


__all__ = ["LayeredCopyFixtureRecipe", "LayeredFixtureConfig", "LayeredFixtureKind"]
