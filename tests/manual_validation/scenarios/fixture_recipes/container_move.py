"""Two-Notebook recipes for reconstructive Section and SectionGroup Move."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from ...runtime import InvariantFailure
from ..common.fixture_builders import ensure_group, ensure_page, ensure_section
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


ContainerKind = Literal["section", "section_group"]


class ContainerMoveFixtureRecipe(RecipeBase):
    recipe_version = 2
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "the Move destination is the exact destination Notebook root",
    )

    def __init__(self, scenario_name: str, container_kind: ContainerKind) -> None:
        self.container_kind = container_kind
        profile = get_scenario_spec(scenario_name).fixture
        source_keys = (
            ("source_section", "source_page")
            if container_kind == "section"
            else ("source_group", "source_section", "source_page")
        )
        source_profile = replace(
            profile,
            name=f"{scenario_name}-source",
            manifest_keys=source_keys,
        )
        destination_keys = ("destination_anchor_a", "destination_anchor_b")
        destination_profile = replace(
            profile,
            name=f"{scenario_name}-destination",
            expected_structure=("destination Notebook root with two typed anchors",),
            manifest_keys=destination_keys,
            creation_tools=frozenset(
                {"create_section"}
                if container_kind == "section"
                else {"create_section_group"}
            ),
            validation_conditions=(
                "destination role is an active distinct Notebook root with two typed anchors",
            ),
        )
        super().__init__(
            scenario_name,
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
            ensure = ensure_section if self.container_kind == "section" else ensure_group
            recorder.record_structure(
                "destination_anchor_a",
                await ensure(context.client, context.notebook_id, "00-Destination-Anchor-A"),
            )
            recorder.record_structure(
                "destination_anchor_b",
                await ensure(context.client, context.notebook_id, "99-Destination-Anchor-B"),
            )
            return FixtureBuildResult(recorder.structure, recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(f"Unsupported container Move role: {context.role}")

        parent_id = context.notebook_id
        if self.container_kind == "section_group":
            group = recorder.record_structure(
                "source_group",
                await ensure_group(context.client, parent_id, "Move-Group-Source"),
            )
            parent_id = str(group["id"])
            section_name = "Move-Group-Section"
            page_title = "Move-Group-Page"
        else:
            section_name = "Move-Section-Source"
            page_title = "Move-Section-Page"

        section = recorder.record_structure(
            "source_section",
            await ensure_section(context.client, parent_id, section_name),
        )
        recorder.record_structure(
            "source_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                page_title,
                "Synthetic container Move source",
            ),
        )
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        if set(build.structure) == {"destination_anchor_a", "destination_anchor_b"}:
            notebook_id = str(context.snapshot.get("notebook_id", ""))
            resolved, _by_id, _checks = resolve_active_structure(
                context.snapshot, build.structure
            )
            expected_type = self.container_kind
            if (
                not notebook_id
                or any(
                    item.get("resource_type") != expected_type
                    or str(item.get("parent_id", "")) != notebook_id
                    for item in resolved.values()
                )
                or len({str(item["id"]) for item in resolved.values()}) != 2
            ):
                raise InvariantFailure("Container Move destination Notebook is not active.")
            return (
                "destination role is an active distinct Notebook root with two typed anchors",
            )

        resolved, _by_id, checks = resolve_active_structure(
            context.snapshot, build.structure
        )
        section = resolved["source_section"]
        page = resolved["source_page"]
        expected_section_parent = (
            resolved["source_group"]["id"]
            if self.container_kind == "section_group"
            else context.snapshot.get("notebook_id")
        )
        checks.require(
            section.get("resource_type") == "section"
            and str(section.get("parent_id")) == str(expected_section_parent)
            and page.get("resource_type") == "page"
            and str(page.get("section_id")) == str(section["id"]),
            "Container Move source topology is invalid.",
            "source container subtree has exact typed IDs and parentage",
        )
        return tuple(checks.checks)

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        if str(source.notebook["id"]) == str(destination.notebook["id"]):
            raise InvariantFailure("Container Move roles resolved to the same Notebook ID.")
        if any(
            str(destination.build.structure[key].get("parent_id", ""))
            != str(destination.notebook["id"])
            for key in ("destination_anchor_a", "destination_anchor_b")
        ):
            raise InvariantFailure("Container Move destination anchor escaped its Notebook.")
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + (
                "cross-Notebook source and destination roles are distinct",
                "destination contains two distinct typed anchors",
            ),
        )


__all__ = ["ContainerMoveFixtureRecipe"]
