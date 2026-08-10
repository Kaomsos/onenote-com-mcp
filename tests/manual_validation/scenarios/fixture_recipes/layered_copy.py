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
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase, evidence


class LayeredFixtureKind(Enum):
    PAGE = "page"
    SECTION = "section"
    SECTION_GROUP = "section_group"
    NOTEBOOK = "notebook"
    MOVE = "move"


@dataclass(frozen=True)
class LayeredFixtureConfig:
    kind: LayeredFixtureKind
    parent_title: str = "Rich-Page"
    semantic_title: str = "List-Tag-Page"


class LayeredCopyFixtureRecipe(RecipeBase):
    def __init__(self, scenario_name: str, config: LayeredFixtureConfig) -> None:
        super().__init__(scenario_name)
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
        parent, copy_fixture = await ensure_copy_rich_fixture(context.client, parent, context.options.run_dir)
        parent = await enforce_page_position(context.client, section["id"], parent["id"], "", 1)
        semantic = await enforce_page_position(context.client, section["id"], semantic["id"], parent["id"], 2)
        semantic, semantic_fixture = await ensure_copy_list_tag_fixture(context.client, semantic)
        copy_fixture["automated_content"] = ["rich_text", "table", "image", "list", "tag"]
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
            r.record_structure("destination_section", await ensure_section(context.client, context.notebook_id, "Destination"))
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
        parent = resolved["disposable_page" if kind is LayeredFixtureKind.MOVE else "parent_page"]
        semantic = resolved["semantic_page"]
        checks.require(parent.get("section_id") == semantic.get("section_id") and int(parent.get("page_level", 0)) == 1 and int(semantic.get("page_level", 0)) == 2 and semantic.get("parent_page_id") == parent["id"], "Layered Copy fixture Page topology is invalid.", "strict parent and semantic child form an isolated two-page subtree")
        copy_fixture = evidence(build, "copy_fixture")
        automated = {str(value).casefold() for value in (copy_fixture or {}).get("automated_content", [])}
        checks.require({"rich_text", "table", "image"}.issubset(automated), "Rich Copy fixture is missing a required automated content capability.", "rich text, table, and image capabilities were created and observed")
        semantic_evidence = (copy_fixture or {}).get("semantic_page")
        checks.require(isinstance(semantic_evidence, dict) and {"List", "Tag"}.issubset(semantic_evidence.get("observed_capabilities", [])) and semantic_evidence.get("observed_counts", {}).get("List") == 3 and semantic_evidence.get("observed_counts", {}).get("Tag") == 3, "Semantic Copy fixture is missing the three generated List/Tag items.", "semantic child contains three generated mixed List/Tag items")
        return tuple(checks.checks)


__all__ = ["LayeredCopyFixtureRecipe", "LayeredFixtureConfig", "LayeredFixtureKind"]
