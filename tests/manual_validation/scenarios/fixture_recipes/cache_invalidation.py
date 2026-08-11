from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class CacheInvalidationRecipe(RecipeBase):
    invalidation_probe = True

    def __init__(self) -> None:
        super().__init__("cache-invalidation")

    async def build(self, context) -> FixtureBuildResult:
        section = context.recorder.record_structure(
            "probe_section",
            await ensure_section(context.client, context.notebook_id, "00-Cache-Invalidation"),
        )
        context.recorder.record_structure(
            "probe_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                "00-Owned-Probe",
                "Synthetic cache invalidation fixture owned by this exact Recipe.",
            ),
        )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(
            resolved["probe_page"].get("section_id") == resolved["probe_section"].get("id"),
            "Cache invalidation probe Page escaped its owned Section.",
            "owned cache invalidation probe topology is exact",
        )
        return tuple(checks.checks)


RECIPE = CacheInvalidationRecipe()
__all__ = ["CacheInvalidationRecipe", "RECIPE"]
