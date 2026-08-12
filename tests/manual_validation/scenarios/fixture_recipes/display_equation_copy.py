"""Programmatic fixture recipe for bounded DisplayEquation Copy."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
)
from .interactive import DisplayEquationInteractiveFixtureRecipe
from .recipe_base import BuildMode, RecipeBase


class DisplayEquationCopyRecipe(DisplayEquationInteractiveFixtureRecipe):
    build_mode = BuildMode.PROGRAMMATIC
    recipe_version = 1
    consumer_scenario = False

    def __init__(self) -> None:
        RecipeBase.__init__(self, "copy-display-equation")

    def validate_registration(self, spec) -> None:
        RecipeBase.validate_registration(self, spec)

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        return await self.build_scaffold(context)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        checks = list(super().validate(context, build))
        page_id = str(build.structure["canvas_page"]["id"])
        report = self.content_report(context.snapshot, page_id)
        if report.get("passed") is not True:
            raise InvariantFailure(
                "Programmatic DisplayEquation fixture failed its exact content detector."
            )
        checks.append(
            "programmatic source exposes one standalone DisplayEquation and the rich base"
        )
        return tuple(checks)


RECIPE = DisplayEquationCopyRecipe()
__all__ = ["DisplayEquationCopyRecipe", "RECIPE"]
