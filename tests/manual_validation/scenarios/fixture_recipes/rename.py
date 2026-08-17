"""Fixture recipe owned by the rename scenario."""

from __future__ import annotations

from ...test_utils import display_name
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
from ..common.fixture_models import FixtureBuildResult, FixtureContext, FixtureValidationContext, resolve_active_structure
from .recipe_base import RecipeBase


class RenameFixtureRecipe(RecipeBase):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__(
            "rename", frozenset({"section_group_target", "section_target", "page_target"})
        )

    def validate_registration(self, spec) -> None:
        if self.scenario_name != spec.name or self.profile != spec.fixture:
            raise ValueError("Rename fixture recipe/profile mismatch.")
        if spec.fixture.manifest_keys != ("section_group_target", "section_target", "page_target"):
            raise ValueError("Rename fixture profile must declare all fixed targets.")
        if self.manifest_keys != frozenset(
            {"section_group_target", "section_target", "page_target"}
        ):
            raise ValueError("Rename fixture recipe has an invalid target key set.")

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        section_group = await ensure_group(
            context.client, context.notebook_id, "Rename-Group"
        )
        section = await ensure_section(
            context.client, section_group["id"], "Rename-Section"
        )
        context.recorder.record_structure("section_group_target", section_group)
        context.recorder.record_structure("section_target", section)
        context.recorder.record_structure(
            "page_target",
            await ensure_page(context.client, section["id"], "Rename-Page", "Rename Page fixture"),
        )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(self, context: FixtureValidationContext, build: FixtureBuildResult) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(
            set(resolved) == {"section_group_target", "section_target", "page_target"},
            "Rename fixture must contain all fixed targets.",
            "fixed SectionGroup, Section, and Page rename targets resolve",
        )
        checks.require(
            resolved["section_group_target"].get("resource_type") == "section_group"
            and resolved["section_target"].get("resource_type") == "section",
            "Rename fixture target types are invalid.",
            "fixed SectionGroup and Section target types are exact",
        )
        checks.require(
            resolved["page_target"].get("resource_type") == "page"
            and resolved["page_target"].get("section_id") == resolved["section_target"]["id"],
            "Rename Page target is not inside the fixed Section target.",
            "fixed Page target belongs to the fixed Section target",
        )
        checks.require(
            resolved["section_target"].get("parent_id")
            == resolved["section_group_target"].get("id"),
            "Rename Section target is not inside the fixed SectionGroup target.",
            "fixed Section target belongs to the fixed SectionGroup target",
        )
        checks.require(
            all(display_name(item) != "" for item in resolved.values()),
            "Rename fixture target has no stable display name.",
            "both fixed rename targets have stable display names",
        )
        return tuple(checks.checks)


RECIPE = RenameFixtureRecipe()

__all__ = ["RECIPE", "RenameFixtureRecipe"]
