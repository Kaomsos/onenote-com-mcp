"""Fresh-only fixture for production COM convergence validation."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ..common.fixture_builders import enforce_page_position, ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class OneNoteConvergenceFixtureRecipe(RecipeBase):
    recipe_version = 1
    supports_cache = False

    def __init__(self) -> None:
        super().__init__("onenote-convergence")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        section = recorder.record_structure(
            "convergence_section",
            await ensure_section(
                context.client, context.notebook_id, "01-Convergence-Section"
            ),
        )
        first = recorder.record_structure(
            "first_anchor_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                "01-Anchor",
                f"Convergence anchor 1: {context.token}",
            ),
        )
        second = recorder.record_structure(
            "second_anchor_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                "02-Anchor",
                f"Convergence anchor 2: {context.token}",
            ),
        )
        recorder.refresh_structure(
            "first_anchor_page",
            await enforce_page_position(
                context.client, str(section["id"]), str(first["id"]), "", 1
            ),
        )
        recorder.refresh_structure(
            "second_anchor_page",
            await enforce_page_position(
                context.client,
                str(section["id"]),
                str(second["id"]),
                str(first["id"]),
                1,
            ),
        )
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(
            context.snapshot, build.structure
        )
        section = resolved["convergence_section"]
        first = resolved["first_anchor_page"]
        second = resolved["second_anchor_page"]
        checks.require(
            first.get("section_id") == section.get("id")
            and second.get("section_id") == section.get("id"),
            "Convergence anchor Pages escaped the declared Section.",
            "both anchor Pages have the exact convergence Section ID",
        )
        checks.require(
            int(first.get("order", -1)) < int(second.get("order", -1))
            and int(first.get("page_level", 0)) == 1
            and int(second.get("page_level", 0)) == 1,
            "Convergence anchor order or level is invalid.",
            "anchor Pages have stable 01,02 root ordering",
        )
        if first.get("id") == second.get("id"):
            raise InvariantFailure("Convergence anchor IDs must be distinct.")
        return tuple(checks.checks)


RECIPE = OneNoteConvergenceFixtureRecipe()

__all__ = ["OneNoteConvergenceFixtureRecipe", "RECIPE"]
