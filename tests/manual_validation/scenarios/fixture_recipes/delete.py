"""Fixture recipe owned by the non-permanent delete scenario."""

from __future__ import annotations

from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


class DeleteFixtureRecipe(RecipeBase):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__("delete")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        sandbox = context.recorder.record_structure("delete_sandbox", await ensure_group(context.client, context.notebook_id, "Delete-Sandbox"))
        target = context.recorder.record_structure(
            "disposable_group",
            await ensure_group(context.client, sandbox["id"], "Disposable-Group"),
        )
        context.recorder.record_structure(
            "disposable_section",
            await ensure_section(context.client, target["id"], "Disposable-Section"),
        )
        section_target = context.recorder.record_structure(
            "disposable_section_target",
            await ensure_section(context.client, sandbox["id"], "Disposable-Section-Target"),
        )
        page_section = context.recorder.record_structure(
            "disposable_page_section",
            await ensure_section(context.client, sandbox["id"], "Disposable-Page-Section"),
        )
        context.recorder.record_structure(
            "disposable_page_target",
            await ensure_page(context.client, page_section["id"], "Disposable-Page-Target", "Disposable Page"),
        )
        context.recorder.record_structure(
            "disposable_page_target_second",
            await ensure_page(
                context.client,
                page_section["id"],
                "Disposable-Page-Target-Second",
                "Disposable Page Second",
            ),
        )
        budget_section = context.recorder.record_structure(
            "budget_section",
            await ensure_section(
                context.client, sandbox["id"], "Budget-Overlimit-Section"
            ),
        )
        for index in range(4):
            context.recorder.record_structure(
                f"budget_page_{index + 1}",
                await ensure_page(
                    context.client,
                    budget_section["id"],
                    f"Budget-Page-{index + 1}",
                    f"Budget page {index + 1}",
                ),
            )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(resolved["disposable_group"].get("parent_id") == resolved["delete_sandbox"]["id"], "Delete target is not a direct descendant of Delete-Sandbox.", "disposable_group is manifest-allowlisted under Delete-Sandbox")
        checks.require(
            resolved["disposable_section"].get("parent_id")
            == resolved["disposable_group"]["id"],
            "Delete target does not contain its persisted sentinel Section.",
            "disposable_group contains a persisted sentinel Section",
        )
        checks.require(
            resolved["disposable_section_target"].get("parent_id") == resolved["delete_sandbox"]["id"]
            and resolved["disposable_page_section"].get("parent_id") == resolved["delete_sandbox"]["id"]
            and resolved["disposable_page_target"].get("section_id") == resolved["disposable_page_section"]["id"],
            "Typed batch Delete targets escaped Delete-Sandbox.",
            "Page, Section, and SectionGroup batch Delete targets are independently allowlisted",
        )
        checks.require(
            resolved["disposable_page_target_second"].get("section_id")
            == resolved["disposable_page_section"]["id"],
            "Second leaf Page batch target escaped the disposable Page Section.",
            "two independent leaf Page targets share the disposable Page Section",
        )
        budget_page_ids = {
            resolved[f"budget_page_{index}"].get("section_id")
            for index in range(1, 5)
        }
        checks.require(
            budget_page_ids == {resolved["budget_section"]["id"]},
            "Batch budget rejection fixture Pages escaped their confirmed Section.",
            "budget_section contains four direct Page descendants",
        )
        return tuple(checks.checks)


RECIPE = DeleteFixtureRecipe()

__all__ = ["DeleteFixtureRecipe", "RECIPE"]
