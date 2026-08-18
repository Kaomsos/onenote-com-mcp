"""Two-Notebook fixture for root-only and full-subtree Page Move safety."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from local_onenote_mcp.page.parser import local_name, parse_xml

from ...runtime import InvariantFailure
from ..common.fixture_builders import enforce_page_position, ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.page_readback import (
    PURE_RICH_TEXT_HTML,
    SPECIAL_PAGE_TITLE,
    THREE_COLUMN_TABLE_HTML,
)
from ..common.specs import get_scenario_spec
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
    RecipeBase,
)


class MovePageFixtureRecipe(RecipeBase):
    recipe_version = 7
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
        self._root_table_observations: list[dict[str, Any]] = []

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
            (
                "root_only_page",
                SPECIAL_PAGE_TITLE,
                THREE_COLUMN_TABLE_HTML,
                "html",
                1,
            ),
            ("root_only_child", "02-Root-Only-Child", "Must remain in source", "plain", 2),
            ("subtree_page", "03-Subtree", PURE_RICH_TEXT_HTML, "html", 1),
            ("subtree_child", "04-Subtree-Child", PURE_RICH_TEXT_HTML, "html", 2),
        )
        previous_id = ""
        for key, title, body, content_format, level in definitions:
            page = await ensure_page(
                context.client,
                section["id"],
                title,
                body,
                content_format=content_format,
            )
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
        root = resolved["root_only_page"]
        projections = context.snapshot.get("page_capability_projections")
        projection = (
            projections.get(str(root["id"]))
            if isinstance(projections, Mapping)
            else None
        )
        capabilities = (
            {str(value) for value in projection.get("capabilities", ())}
            if isinstance(projection, Mapping)
            else set()
        )
        checks.require(
            str(root.get("title", "")) == SPECIAL_PAGE_TITLE,
            "Move source title lost special characters during fixture creation.",
            "default-title Move source preserves the exact special-character title",
        )
        checks.require(
            isinstance(projection, Mapping)
            and projection.get("complete") is True
            and not projection.get("unknown_nodes")
            and not projection.get("unsupported_page_roots")
            and {"Outline", "RichText", "Table"}.issubset(capabilities),
            "Move source lacks a complete RichText/Table projection.",
            "default-title Move source exposes complete Outline/RichText/Table capabilities",
        )
        pure_rich_pages = [resolved["subtree_page"], resolved["subtree_child"]]
        pure_rich_projections = [
            projections.get(str(page["id"]))
            if isinstance(projections, Mapping)
            else None
            for page in pure_rich_pages
        ]
        checks.require(
            all(
                isinstance(value, Mapping)
                and value.get("complete") is True
                and not value.get("unknown_nodes")
                and not value.get("unsupported_page_roots")
                and {"Outline", "RichText"}.issubset(
                    {str(capability) for capability in value.get("capabilities", ())}
                )
                and "Table"
                not in {
                    str(capability) for capability in value.get("capabilities", ())
                }
                for value in pure_rich_projections
            ),
            "Move subtree lacks two complete pure RichText projections.",
            "subtree Move source and child expose complete pure RichText projections",
        )
        return tuple(checks.checks)

    def begin_snapshot_content_validation(self) -> None:
        self._root_table_observations = []

    def snapshot_page_observer(
        self,
        role: str,
        build: FixtureBuildResult,
    ):
        root_id = str(build.structure.get("root_only_page", {}).get("id", ""))

        def observe(page: Mapping[str, Any], xml: str) -> None:
            if role != "source" or str(page.get("id", "")) != root_id:
                return
            root = parse_xml(xml)
            tables = [node for node in root.iter() if local_name(node.tag) == "Table"]
            columns = [node for node in root.iter() if local_name(node.tag) == "Column"]
            rows = [node for node in root.iter() if local_name(node.tag) == "Row"]
            cells = [node for node in root.iter() if local_name(node.tag) == "Cell"]
            widths: list[Decimal | None] = []
            for column in columns:
                try:
                    value = Decimal(str(column.attrib.get("width", "")))
                except InvalidOperation:
                    value = None
                widths.append(
                    value
                    if value is not None and value.is_finite() and value > 0
                    else None
                )
            self._root_table_observations.append(
                {
                    "table_count": len(tables),
                    "column_count": len(columns),
                    "row_count": len(rows),
                    "cell_count": len(cells),
                    "all_column_widths_positive_finite": bool(widths)
                    and all(value is not None for value in widths),
                    "content_exposed": False,
                }
            )

        return observe

    def complete_snapshot_content_validation(self) -> None:
        if self._root_table_observations != [
            {
                "table_count": 1,
                "column_count": 3,
                "row_count": 2,
                "cell_count": 6,
                "all_column_widths_positive_finite": True,
                "content_exposed": False,
            }
        ]:
            raise InvariantFailure(
                "Move source did not retain the exact one-table/three-column/two-row fixture shape."
            )

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
