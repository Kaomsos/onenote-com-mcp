"""Fixture recipe owned by the create scenario."""

from __future__ import annotations

from ..common.fixture_builders import ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class CreateFixtureRecipe(RecipeBase):
    recipe_version = 5

    def __init__(self) -> None:
        super().__init__("create")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        r = context.recorder
        r.record_structure(
            "duplicate_title_section",
            await ensure_section(
                context.client,
                context.notebook_id,
                "Duplicate-Title-Target",
            ),
        )
        return FixtureBuildResult(r.structure, r.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        section = resolved["duplicate_title_section"]
        checks.require(
            section.get("resource_type") == "section"
            and section.get("parent_id") == context.snapshot.get("notebook_id"),
            "Create fixture Duplicate-Title-Target escaped its Notebook.",
            "Duplicate-Title-Target is the manifest-bound target Section",
        )
        return tuple(checks.checks)


RECIPE = CreateFixtureRecipe()

__all__ = ["CreateFixtureRecipe", "RECIPE"]
