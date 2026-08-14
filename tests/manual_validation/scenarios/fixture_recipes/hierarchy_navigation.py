"""Cache-capable fixture for hierarchy navigation tool validation."""

from __future__ import annotations

from ..common.fixture_builders import (
    enforce_page_position_with_query as enforce_page_position,
    ensure_group_with_query as ensure_group,
    ensure_page_with_query as ensure_page,
    ensure_section_with_query as ensure_section,
)
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class HierarchyNavigationFixtureRecipe(RecipeBase):
    recipe_version = 1

    def __init__(self) -> None:
        super().__init__("hierarchy-navigation")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        group = recorder.record_structure(
            "navigation_group",
            await ensure_group(
                context.client, context.notebook_id, "Navigation-Group"
            ),
        )
        section = recorder.record_structure(
            "navigation_section",
            await ensure_section(
                context.client, str(group["id"]), "Navigation-Section"
            ),
        )
        recorder.record_structure(
            "navigation_section_sibling",
            await ensure_section(
                context.client, str(group["id"]), "Navigation-Section-Sibling"
            ),
        )
        parent = recorder.record_structure(
            "navigation_parent_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                "Navigation-Parent",
                "navigation parent page",
            ),
        )
        child = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Child",
            "navigation child page",
        )
        child = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(child["id"]),
            str(parent["id"]),
            2,
        )
        recorder.record_structure("navigation_child_page", child)
        grandchild = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Grandchild",
            "navigation grandchild page",
        )
        grandchild = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(grandchild["id"]),
            str(child["id"]),
            3,
        )
        recorder.record_structure("navigation_grandchild_page", grandchild)
        child_sibling = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Child-Sibling",
            "navigation child sibling page",
        )
        child_sibling = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(child_sibling["id"]),
            str(grandchild["id"]),
            2,
        )
        recorder.record_structure("navigation_child_page_sibling", child_sibling)
        root_sibling = await ensure_page(
            context.client,
            str(section["id"]),
            "Navigation-Root-Sibling",
            "navigation root sibling page",
        )
        root_sibling = await enforce_page_position(
            context.client,
            str(section["id"]),
            str(root_sibling["id"]),
            str(child_sibling["id"]),
            1,
        )
        recorder.record_structure("navigation_root_page_sibling", root_sibling)
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(
            context.snapshot, build.structure
        )
        notebook_id = str(context.snapshot.get("notebook_id", ""))
        group = resolved["navigation_group"]
        section = resolved["navigation_section"]
        section_sibling = resolved["navigation_section_sibling"]
        parent = resolved["navigation_parent_page"]
        child = resolved["navigation_child_page"]
        grandchild = resolved["navigation_grandchild_page"]
        child_sibling = resolved["navigation_child_page_sibling"]
        root_sibling = resolved["navigation_root_page_sibling"]
        checks.require(
            group.get("resource_type") == "section_group"
            and group.get("parent_id") == notebook_id
            and section.get("resource_type") == "section"
            and section.get("parent_id") == group.get("id")
            and section_sibling.get("resource_type") == "section"
            and section_sibling.get("parent_id") == group.get("id"),
            "Hierarchy navigation container topology is invalid.",
            "Notebook/SectionGroup/two-Section container ancestry is exact",
        )
        checks.require(
            all(
                page.get("resource_type") == "page"
                and page.get("section_id") == section.get("id")
                for page in (
                    parent,
                    child,
                    grandchild,
                    child_sibling,
                    root_sibling,
                )
            )
            and (parent.get("page_level"), parent.get("parent_page_id"))
            == (1, None)
            and (child.get("page_level"), child.get("parent_page_id"))
            == (2, parent.get("id"))
            and (
                grandchild.get("page_level"),
                grandchild.get("parent_page_id"),
            )
            == (3, child.get("id"))
            and (
                child_sibling.get("page_level"),
                child_sibling.get("parent_page_id"),
            )
            == (2, parent.get("id"))
            and (
                root_sibling.get("page_level"),
                root_sibling.get("parent_page_id"),
            )
            == (1, None),
            "Hierarchy navigation Page indentation topology is invalid.",
            "Page levels 1/2/3 derive the exact branched indentation tree",
        )
        page_hashes = context.snapshot.get("page_hashes", {})
        checks.require(
            isinstance(page_hashes, dict)
            and all(
                str(page["id"]) in page_hashes
                for page in (
                    parent,
                    child,
                    grandchild,
                    child_sibling,
                    root_sibling,
                )
            ),
            "Hierarchy navigation fixture lacks complete Page content evidence.",
            "every navigation Page was content-validated once by the fixture snapshot",
        )
        return tuple(checks.checks)


RECIPE = HierarchyNavigationFixtureRecipe()

__all__ = ["HierarchyNavigationFixtureRecipe", "RECIPE"]
