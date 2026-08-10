"""Fixture recipe owned by the non-permanent delete scenario."""

from __future__ import annotations

from ..common.fixture_builders import ensure_group
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


class DeleteFixtureRecipe(RecipeBase):
    def __init__(self) -> None:
        super().__init__("delete")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        sandbox = context.recorder.record_structure("delete_sandbox", await ensure_group(context.client, context.notebook_id, "Delete-Sandbox"))
        context.recorder.record_structure("disposable_group", await ensure_group(context.client, sandbox["id"], "Disposable-Group"))
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(resolved["disposable_group"].get("parent_id") == resolved["delete_sandbox"]["id"], "Delete target is not a direct descendant of Delete-Sandbox.", "disposable_group is manifest-allowlisted under Delete-Sandbox")
        return tuple(checks.checks)


RECIPE = DeleteFixtureRecipe()

__all__ = ["DeleteFixtureRecipe", "RECIPE"]
