from __future__ import annotations

from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class CopyNotebookFixtureRecipe(LayeredCopyFixtureRecipe):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__("copy-notebook", LayeredFixtureConfig(LayeredFixtureKind.NOTEBOOK))

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        await super().build(context)
        group = context.recorder.record_structure(
            "source_group",
            await ensure_group(context.client, context.notebook_id, "Source-Group"),
        )
        section = context.recorder.record_structure(
            "group_section",
            await ensure_section(context.client, group["id"], "Grouped-Section"),
        )
        context.recorder.record_structure(
            "group_page",
            await ensure_page(
                context.client,
                section["id"],
                "Grouped-Page",
                f"Nested SectionGroup Copy token: {context.token}",
            ),
        )
        return FixtureBuildResult(
            context.recorder.structure,
            context.recorder.evidence,
        )

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        checks = list(super().validate(context, build))
        resolved, _by_id, state = resolve_active_structure(
            context.snapshot,
            build.structure,
        )
        state.require(
            resolved["source_group"].get("parent_id") == context.snapshot.get("notebook_id")
            and resolved["group_section"].get("parent_id")
            == resolved["source_group"]["id"]
            and resolved["group_page"].get("section_id")
            == resolved["group_section"]["id"],
            "Copy Notebook fixture SectionGroup subtree is missing or misplaced.",
            "source Notebook contains a manifest-bound SectionGroup/Section/Page subtree",
        )
        checks.append(
            "source Notebook contains a manifest-bound SectionGroup/Section/Page subtree"
        )
        return tuple(checks)

RECIPE = CopyNotebookFixtureRecipe()
__all__ = ["CopyNotebookFixtureRecipe", "RECIPE"]
