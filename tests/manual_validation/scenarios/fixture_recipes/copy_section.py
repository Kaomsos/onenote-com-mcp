from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_group, ensure_section
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
    recipe_version = 4
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
            "same_notebook_anchor_a",
            "same_notebook_anchor_b",
        )
        destination_keys = (
            "cross_notebook_group",
            "cross_notebook_anchor_a",
            "cross_notebook_anchor_b",
        )
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
            group = context.recorder.record_structure(
                "cross_notebook_group",
                await ensure_group(
                    context.client,
                    context.notebook_id,
                    "Cross-Notebook-Group",
                ),
            )
            for key, name in (
                ("cross_notebook_anchor_a", "00-Section-Anchor-A"),
                ("cross_notebook_anchor_b", "99-Section-Anchor-B"),
            ):
                context.recorder.record_structure(
                    key,
                    await ensure_section(context.client, group["id"], name),
                )
            return FixtureBuildResult(
                context.recorder.structure,
                context.recorder.evidence,
            )
        if context.role != "source":
            raise InvariantFailure(f"Unsupported Copy Section Notebook role: {context.role}")
        build = await super().build(context)
        group = build.structure["group_b"]
        for key, name in (
            ("same_notebook_anchor_a", "00-Section-Anchor-A"),
            ("same_notebook_anchor_b", "99-Section-Anchor-B"),
        ):
            context.recorder.record_structure(
                key,
                await ensure_section(context.client, group["id"], name),
            )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if set(build.structure) == {
            "cross_notebook_group",
            "cross_notebook_anchor_a",
            "cross_notebook_anchor_b",
        }:
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
            checks.require(
                all(
                    resolved[key].get("resource_type") == "section"
                    and resolved[key].get("parent_id") == group["id"]
                    for key in ("cross_notebook_anchor_a", "cross_notebook_anchor_b")
                ),
                "Cross-Notebook Section anchors escaped their destination Group.",
                "cross-Notebook destination Group contains two Section anchors",
            )
            return tuple(checks.checks)
        checks = list(super().validate(context, build))
        resolved, _by_id, state = resolve_active_structure(context.snapshot, build.structure)
        state.require(
            all(
                resolved[key].get("resource_type") == "section"
                and resolved[key].get("parent_id") == resolved["group_b"]["id"]
                for key in ("same_notebook_anchor_a", "same_notebook_anchor_b")
            ),
            "Same-Notebook Section anchors escaped their destination Group.",
            "same-Notebook destination Group contains two Section anchors",
        )
        checks.append("same-Notebook destination Group contains two Section anchors")
        return tuple(checks)

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
