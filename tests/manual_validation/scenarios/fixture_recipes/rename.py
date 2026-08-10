"""Fixture recipe owned by the rename scenario."""

from __future__ import annotations

import argparse

from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


class RenameFixtureRecipe(RecipeBase):
    def __init__(self) -> None:
        super().__init__("rename", frozenset({"group_a", "group_b", "content_section"}))

    def required_manifest_keys(self, args: argparse.Namespace) -> frozenset[str]:
        return frozenset({args.target})

    def validate_registration(self, spec) -> None:
        if self.scenario_name != spec.name or self.profile != spec.fixture:
            raise ValueError("Rename fixture recipe/profile mismatch.")
        if spec.fixture.manifest_keys != ("one_of(group_a,group_b,content_section)",):
            raise ValueError("Rename fixture profile must declare its one-of key contract.")
        if self.manifest_keys != frozenset({"group_a", "group_b", "content_section"}):
            raise ValueError("Rename fixture recipe has an invalid target key set.")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        target_key = context.args.target
        if target_key == "content_section":
            group = await ensure_group(context.client, context.notebook_id, "Rename-Group")
            target = await ensure_section(context.client, group["id"], "Content-Section")
        else:
            name = "Group-A" if target_key == "group_a" else "Group-B"
            target = await ensure_group(context.client, context.notebook_id, name)
        context.recorder.record_structure(target_key, target)
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(len(resolved) == 1, "Rename fixture must contain exactly one selected target.", "exactly one CLI-selected rename target key was created")
        checks.require(display_name(next(iter(resolved.values()))) != "", "Rename fixture selected target has no stable display name.", "selected rename target has a stable display name")
        return tuple(checks.checks)


RECIPE = RenameFixtureRecipe()

__all__ = ["RECIPE", "RenameFixtureRecipe"]
