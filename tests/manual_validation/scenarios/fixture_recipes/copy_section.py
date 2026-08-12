from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_group
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

class CopySectionFixtureRecipe(LayeredCopyFixtureRecipe):
    recipe_version = 3
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "same-Notebook and cross-Notebook destination Groups belong to their declared roles",
    )

    def __init__(self) -> None:
        source_keys = (
            "group_a",
            "group_b",
            "source_section",
            "parent_page",
            "semantic_page",
        )
        destination_keys = ("cross_notebook_group",)
        profile = get_scenario_spec("copy-section").fixture
        source_profile = replace(
            profile,
            name="rich-section-copy-source",
            expected_structure=profile.expected_structure[:-1],
            manifest_keys=source_keys,
            validation_conditions=profile.validation_conditions[:-1],
        )
        destination_profile = replace(
            profile,
            name="copy-section-destination",
            expected_structure=("destination:Cross-Notebook-Group",),
            content_capabilities=(),
            manifest_keys=destination_keys,
            creation_tools=frozenset({"create_section_group"}),
            validation_conditions=(
                "cross-Notebook destination Group is active under the destination Notebook",
            ),
        )
        super().__init__(
            "copy-section",
            LayeredFixtureConfig(LayeredFixtureKind.SECTION),
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
                "cross_notebook_group",
                await ensure_group(
                    context.client,
                    context.notebook_id,
                    "Cross-Notebook-Group",
                ),
            )
            return FixtureBuildResult(
                context.recorder.structure,
                context.recorder.evidence,
            )
        if context.role != "source":
            raise InvariantFailure(f"Unsupported Copy Section Notebook role: {context.role}")
        return await super().build(context)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if set(build.structure) == {"cross_notebook_group"}:
            resolved, _by_id, checks = resolve_active_structure(
                context.snapshot,
                build.structure,
            )
            group = resolved["cross_notebook_group"]
            checks.require(
                group.get("resource_type") == "section_group"
                and group.get("parent_id") == context.snapshot.get("notebook_id"),
                "Cross-Notebook Section destination Group escaped its destination Notebook.",
                "cross-Notebook destination Group belongs to the destination role",
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
        internal_group = source.build.structure["group_b"]
        cross_group = destination.build.structure["cross_notebook_group"]
        if str(internal_group.get("parent_id", "")) != str(source.notebook["id"]):
            raise InvariantFailure("Same-Notebook Section destination escaped the source role.")
        if str(cross_group.get("parent_id", "")) != str(destination.notebook["id"]):
            raise InvariantFailure("Cross-Notebook Section destination escaped its role.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "same-Notebook Section destination is bound to the source role",
                "cross-Notebook Section destination is bound to the destination role",
            ),
        )

RECIPE = CopySectionFixtureRecipe()
__all__ = ["CopySectionFixtureRecipe", "RECIPE"]
