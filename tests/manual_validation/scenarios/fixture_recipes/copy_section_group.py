from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
)

class CopySectionGroupFixtureRecipe(LayeredCopyFixtureRecipe):
    recipe_version = 3
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "same-Notebook and cross-Notebook roots belong to their declared roles",
    )

    def __init__(self) -> None:
        source_keys = ("group_a", "source_section", "parent_page", "semantic_page")
        destination_keys = ("cross_notebook_anchor_section",)
        profile = get_scenario_spec("copy-section-group").fixture
        source_profile = replace(
            profile,
            name="rich-group-copy-source",
            expected_structure=profile.expected_structure[:-1],
            manifest_keys=source_keys,
            validation_conditions=profile.validation_conditions[:-1],
        )
        destination_profile = replace(
            profile,
            name="copy-section-group-destination",
            expected_structure=("destination:Cross-Notebook-Anchor",),
            content_capabilities=(),
            manifest_keys=destination_keys,
            creation_tools=frozenset({"create_section"}),
            validation_conditions=(
                "destination anchor Section is active under the destination Notebook",
            ),
        )
        super().__init__(
            "copy-section-group",
            LayeredFixtureConfig(LayeredFixtureKind.SECTION_GROUP),
            notebook_roles=(
                NotebookRoleSpec(
                    "destination",
                    destination_profile,
                    {"manifest_keys": list(destination_keys)},
                ),
                NotebookRoleSpec(
                    "source",
                    source_profile,
                    {"manifest_keys": list(source_keys)},
                ),
            ),
        )

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        if context.role == "destination":
            context.recorder.record_structure(
                "cross_notebook_anchor_section",
                await ensure_section(
                    context.client,
                    context.notebook_id,
                    "Cross-Notebook-Anchor",
                ),
            )
            return FixtureBuildResult(
                context.recorder.structure,
                context.recorder.evidence,
            )
        if context.role != "source":
            raise InvariantFailure(
                f"Unsupported Copy SectionGroup Notebook role: {context.role}"
            )
        return await super().build(context)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if set(build.structure) == {"cross_notebook_anchor_section"}:
            resolved, _by_id, checks = resolve_active_structure(
                context.snapshot,
                build.structure,
            )
            section = resolved["cross_notebook_anchor_section"]
            checks.require(
                section.get("resource_type") == "section"
                and section.get("parent_id") == context.snapshot.get("notebook_id"),
                "Cross-Notebook SectionGroup destination anchor escaped its Notebook.",
                "destination anchor Section belongs to the destination role",
            )
            return tuple(checks.checks)
        return super().validate(context, build)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        source_group = source.build.structure["group_a"]
        anchor = destination.build.structure["cross_notebook_anchor_section"]
        if str(source_group.get("parent_id", "")) != str(source.notebook["id"]):
            raise InvariantFailure("SectionGroup Copy source escaped the source role.")
        if str(anchor.get("parent_id", "")) != str(destination.notebook["id"]):
            raise InvariantFailure("SectionGroup Copy destination escaped its role.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "same-Notebook SectionGroup destination is the source Notebook root",
                "cross-Notebook SectionGroup destination is the destination Notebook root",
            ),
        )

RECIPE = CopySectionGroupFixtureRecipe()
__all__ = ["CopySectionGroupFixtureRecipe", "RECIPE"]
