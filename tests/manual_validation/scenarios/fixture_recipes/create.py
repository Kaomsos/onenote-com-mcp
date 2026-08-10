"""Fixture recipe owned by the create scenario."""

from __future__ import annotations

from ..common.fixture_builders import (
    enforce_page_position,
    ensure_copy_rich_fixture,
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
from .recipe_base import RecipeBase


class CreateFixtureRecipe(RecipeBase):
    def __init__(self) -> None:
        super().__init__("create")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        group_a = r.record_structure("group_a", await ensure_group(context.client, context.notebook_id, "Group-A"))
        r.record_structure("group_b", await ensure_group(context.client, context.notebook_id, "Group-B"))
        delete_sandbox = r.record_structure("delete_sandbox", await ensure_group(context.client, context.notebook_id, "Delete-Sandbox"))
        content_section = r.record_structure("content_section", await ensure_section(context.client, group_a["id"], "Content-Section"))
        r.record_structure("disposable_group", await ensure_group(context.client, delete_sandbox["id"], "Disposable-Group"))
        disposable_section = r.record_structure("disposable_section", await ensure_section(context.client, delete_sandbox["id"], "Disposable-Section"))
        parent = await ensure_page(context.client, content_section["id"], "Parent", f"Parent smoke token: {context.token}")
        child = await ensure_page(context.client, content_section["id"], "Child", f"Child smoke token: {context.token}")
        sibling = await ensure_page(context.client, content_section["id"], "Sibling", f"Sibling smoke token: {context.token}")
        disposable_page = await ensure_page(context.client, disposable_section["id"], "Disposable-Page", f"Disposable smoke token: {context.token}")
        r.record_structure("parent_page", parent)
        r.record_structure("child_page", child)
        r.record_structure("sibling_page", sibling)
        r.record_structure("disposable_page", disposable_page)
        parent = await enforce_page_position(context.client, content_section["id"], parent["id"], "", 1)
        child = await enforce_page_position(context.client, content_section["id"], child["id"], parent["id"], 2)
        sibling = await enforce_page_position(context.client, content_section["id"], sibling["id"], child["id"], 1)
        parent, copy_fixture = await ensure_copy_rich_fixture(context.client, parent, context.options.run_dir)
        r.refresh_structure("parent_page", parent)
        r.refresh_structure("child_page", child)
        r.refresh_structure("sibling_page", sibling)
        r.record_evidence("copy_fixture", copy_fixture)
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        parent, child, sibling = (resolved[key] for key in ("parent_page", "child_page", "sibling_page"))
        section = resolved["content_section"]
        checks.require(
            all(page.get("section_id") == section["id"] for page in (parent, child, sibling)),
            "Fixture Page tree is not contained by the declared source Section.",
            "Parent/Child/Sibling share the declared source Section",
        )
        checks.require(
            int(parent.get("page_level", 0)) == 1
            and int(child.get("page_level", 0)) == 2
            and int(sibling.get("page_level", 0)) == 1
            and child.get("parent_page_id") == parent["id"]
            and sibling.get("parent_page_id") in {None, ""},
            "Fixture Parent/Child/Sibling Page topology is invalid.",
            "Page levels and derived parent relationships match the profile",
        )
        checks.require(section.get("parent_id") == resolved["group_a"]["id"], "Create fixture Content-Section is outside Group-A.", "Content-Section is a child of Group-A")
        checks.require(
            resolved["disposable_group"].get("parent_id") == resolved["delete_sandbox"]["id"]
            and resolved["disposable_section"].get("parent_id") == resolved["delete_sandbox"]["id"]
            and resolved["disposable_page"].get("section_id") == resolved["disposable_section"]["id"],
            "Create fixture disposable targets escaped Delete-Sandbox.",
            "disposable targets are descendants of Delete-Sandbox",
        )
        return tuple(checks.checks)


RECIPE = CreateFixtureRecipe()

__all__ = ["CreateFixtureRecipe", "RECIPE"]
