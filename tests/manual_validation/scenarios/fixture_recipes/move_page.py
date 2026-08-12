"""Two-Notebook fixture for root-only and full-subtree Page Move safety."""

from __future__ import annotations

from dataclasses import replace

from ...runtime import InvariantFailure
from ..common.fixture_builders import enforce_page_position, ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
    RecipeBase,
)


class MovePageFixtureRecipe(RecipeBase):
    recipe_version = 5
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "both Move targets belong only to the destination Notebook role",
    )

    def __init__(self) -> None:
        profile = get_scenario_spec("move-page").fixture
        source_keys = (
            "source_section",
            "root_only_page",
            "root_only_child",
            "subtree_page",
            "subtree_child",
        )
        destination_keys = (
            "destination_section",
            "destination_anchor_a",
            "destination_anchor_b",
        )
        source_profile = replace(
            profile,
            name="page-move-source",
            expected_structure=(
                "Source/01-Root-Only/02-Root-Only-Child",
                "Source/03-Subtree/04-Subtree-Child",
            ),
            manifest_keys=source_keys,
            validation_conditions=(
                "two independent source Page subtrees have exact IDs and levels",
            ),
        )
        destination_profile = replace(
            profile,
            name="page-move-destination",
            expected_structure=("Destination",),
            manifest_keys=destination_keys,
            validation_conditions=(
                "cross-Notebook destination is an active root Section",
            ),
        )
        super().__init__(
            "move-page",
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
        recorder = context.recorder
        if context.role == "destination":
            destination = recorder.record_structure(
                "destination_section",
                await ensure_section(context.client, context.notebook_id, "Destination"),
            )
            recorder.record_structure(
                "destination_anchor_a",
                await ensure_page(
                    context.client,
                    destination["id"],
                    "00-Destination-Anchor-A",
                    f"Move destination anchor A: {context.token}",
                ),
            )
            recorder.record_structure(
                "destination_anchor_b",
                await ensure_page(
                    context.client,
                    destination["id"],
                    "99-Destination-Anchor-B",
                    f"Move destination anchor B: {context.token}",
                ),
            )
            return FixtureBuildResult(recorder.structure, recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(f"Unsupported Move Page Notebook role: {context.role}")

        section = recorder.record_structure(
            "source_section",
            await ensure_section(context.client, context.notebook_id, "Source"),
        )
        definitions = (
            ("root_only_page", "01-Root-Only", "Synthetic root-only Move source", 1),
            ("root_only_child", "02-Root-Only-Child", "Must remain in source", 2),
            ("subtree_page", "03-Subtree", "Synthetic subtree Move source", 1),
            ("subtree_child", "04-Subtree-Child", "Moves with subtree root", 2),
        )
        previous_id = ""
        for key, title, body, level in definitions:
            page = await ensure_page(context.client, section["id"], title, body)
            page = await enforce_page_position(
                context.client,
                section["id"],
                page["id"],
                previous_id,
                level,
            )
            recorder.record_structure(key, page)
            previous_id = str(page["id"])
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        if set(build.structure) == {
            "destination_section",
            "destination_anchor_a",
            "destination_anchor_b",
        }:
            destination = resolved["destination_section"]
            anchors = [
                resolved["destination_anchor_a"],
                resolved["destination_anchor_b"],
            ]
            checks.require(
                destination.get("resource_type") == "section"
                and all(
                    anchor.get("resource_type") == "page"
                    and anchor.get("section_id") == destination["id"]
                    for anchor in anchors
                )
                and len({anchor["id"] for anchor in anchors}) == 2,
                "Move destination is not an active Section.",
                "destination role exposes one active root Section with two Page anchors",
            )
            return tuple(checks.checks)

        section_id = resolved["source_section"]["id"]
        expected = (
            ("root_only_page", 1, None),
            ("root_only_child", 2, "root_only_page"),
            ("subtree_page", 1, None),
            ("subtree_child", 2, "subtree_page"),
        )
        checks.require(
            all(
                resolved[key].get("section_id") == section_id
                and int(resolved[key].get("page_level", 0)) == level
                and resolved[key].get("parent_page_id")
                == (resolved[parent_key]["id"] if parent_key else None)
                for key, level, parent_key in expected
            ),
            "Move source Page topology is invalid.",
            "two independent source Page subtrees have exact IDs and levels",
        )
        return tuple(checks.checks)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        destination_section = destination.build.structure["destination_section"]
        destination_anchors = [
            destination.build.structure["destination_anchor_a"],
            destination.build.structure["destination_anchor_b"],
        ]
        if str(source.notebook["id"]) == str(destination.notebook["id"]):
            raise InvariantFailure("Move Page bundle roles resolved to the same Notebook ID.")
        if str(destination_section.get("parent_id", "")) != str(destination.notebook["id"]):
            raise InvariantFailure("Move destination Section escaped the destination role.")
        if any(
            str(anchor.get("section_id", "")) != str(destination_section["id"])
            for anchor in destination_anchors
        ):
            raise InvariantFailure("Move destination anchor escaped its Section.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "cross-Notebook destination is bound to the destination role",
                "destination contains two distinct Page anchors",
            ),
        )


RECIPE = MovePageFixtureRecipe()
__all__ = ["MovePageFixtureRecipe", "RECIPE"]
