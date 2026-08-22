from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class ComRefreshMutationRecipe(RecipeBase):
    dry_run_scenario_target = (
        "same-MCP OneNote close, bounded native fully-stopped wait, launch recovery, "
        "harness-owned internal and lifecycle COM refresh with exact Page XML and "
        "Notebook probes, target-page baseline stability, one unique Page "
        "rename with durable observation, and exact close before MCP teardown"
    )

    def __init__(self) -> None:
        super().__init__("com-refresh-mutation")

    async def build(self, context) -> FixtureBuildResult:
        section = context.recorder.record_structure(
            "probe_section",
            await ensure_section(context.client, context.notebook_id, "00-COM-Refresh"),
        )
        context.recorder.record_structure(
            "page_target",
            await ensure_page(
                context.client,
                str(section["id"]),
                "00-Owned-Page",
                "Synthetic COM refresh mutation fixture owned by this exact Recipe.",
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
            resolved["page_target"].get("section_id") == resolved["probe_section"].get("id"),
            "COM refresh mutation Page escaped its owned Section.",
            "owned COM refresh mutation topology is exact",
        )
        return tuple(checks.checks)


RECIPE = ComRefreshMutationRecipe()
__all__ = ["ComRefreshMutationRecipe", "RECIPE"]
