"""Fresh disposable fixture for the verified Page ``dateTime`` smoke check."""

from __future__ import annotations

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import RecipeBase


class TimestampFidelityFixtureRecipe(RecipeBase):
    recipe_version = 1
    supports_cache = False
    fresh_only_reason = "Verified Page dateTime smoke checks require fresh disposable Pages."

    def __init__(self) -> None:
        super().__init__(
            "timestamp-fidelity-probe",
            frozenset(
                {
                    "section_target",
                    "page_hierarchy_target",
                    "page_content_target",
                }
            ),
        )

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        section = context.recorder.record_structure(
            "section_target",
            await ensure_section(context.client, context.notebook_id, "Timestamp-Section"),
        )
        context.recorder.record_structure(
            "page_hierarchy_target",
            await ensure_page(
                context.client,
                section["id"],
                "Timestamp-Hierarchy-Page",
                "Timestamp fidelity hierarchy route fixture.",
            ),
        )
        context.recorder.record_structure(
            "page_content_target",
            await ensure_page(
                context.client,
                section["id"],
                "Timestamp-PageContent-Page",
                "Timestamp fidelity page-content route fixture.",
            ),
        )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        section = resolved["section_target"]
        hierarchy_page = resolved["page_hierarchy_target"]
        page_content_page = resolved["page_content_target"]
        checks.require(
            section.get("resource_type") == "section"
            and section.get("parent_id") == context.snapshot.get("notebook_id"),
            "Timestamp probe Section is not an exact Notebook child.",
            "exact Section target belongs to the disposable Notebook",
        )
        checks.require(
            all(
                page.get("resource_type") == "page"
                and page.get("section_id") == section.get("id")
                and page.get("parent_page_id") is None
                for page in (hierarchy_page, page_content_page)
            )
            and hierarchy_page.get("id") != page_content_page.get("id"),
            "Timestamp probe Page targets are missing, nested, or not distinct.",
            "two distinct root Page targets belong to the exact Section",
        )
        if any(not item.get("id") for item in resolved.values()):
            raise InvariantFailure("Timestamp probe fixture resolved an empty exact ID.")
        return tuple(checks.checks)


RECIPE = TimestampFidelityFixtureRecipe()

__all__ = ["RECIPE", "TimestampFidelityFixtureRecipe"]
