"""Human-gated intake of one complete UI-moved Page fixture."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .interactive import AuthoredTemplateInstance, AuthoringZoneSpec, UserAuthoredRecipe
from .move_page_content import MovePageContentRecipe
from .recipe_base import FixtureBundleObservation, FixtureValidationReport, NotebookRoleSpec


INTERACTIVE_SCENARIO = "interactive-move-page"
CACHE_RECIPE_NAME = "interactive-move-page"
PLACEHOLDER_TITLE = "01-Whole-Page-Intake-Placeholder"


class InteractiveMovePageRecipe(MovePageContentRecipe):
    """Freeze a complete UI-imported Page plus an isolated Move destination."""

    capability = "MovePage"
    recipe_version = 3
    synthetic_content_only = False
    expose_revision_marker_values = True
    authored_identity_rebind_keys = frozenset({"source_canvas_page"})
    interactive_checkpoint_manifest_key = "source_canvas_section"
    interactive_checkpoint_action = (
        "moving one complete disposable Page into the exact 01-Whole-Page-Intake Section"
    )
    dry_run_scaffold_target = (
        "run-owned instructions plus an empty exact 01-Whole-Page-Intake Section"
    )
    dry_run_checkpoint_target = (
        "one complete disposable Page transferred into the exact intake Section by the user"
    )
    dry_run_scenario_target = (
        "one production move_page call from the exact materialized intake Page into the "
        "cross-Notebook destination"
    )
    authoring_instruction = (
        "Use OneNote Desktop's Move or Copy dialog to transfer one complete, non-sensitive, "
        "disposable Page into the exact 01-Whole-Page-Intake Section. The Page must be a "
        "root leaf and must retain at least one body authorship/revision marker. Move or copy "
        "the whole Page object; do not recreate its content on the placeholder Page, do not "
        "edit it after transfer, and do not alter the reserved instruction Pages. If the "
        "original matters, first make a disposable duplicate and transfer that duplicate."
    )
    authoring_zones = (
        AuthoringZoneSpec(
            role="source",
            manifest_key="source_canvas_section",
            allowed_operations=("move_or_copy_whole_page_into_section",),
        ),
    )
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique and run-scoped",
        "one complete imported Page is the only Page in the exact intake Section",
        "the scaffold placeholder remains outside the intake Section",
        "the Move destination belongs only to the destination Notebook role",
    )

    def __init__(self) -> None:
        profile = get_scenario_spec(INTERACTIVE_SCENARIO).fixture
        keys = (
            "source_instructions_section",
            "source_instructions_page",
            "source_canvas_section",
            "source_canvas_page",
        )
        destination_keys = (
            "destination_section",
            "destination_anchor",
        )
        role_profile = replace(
            profile,
            name="interactive-move-page-source",
            expected_structure=(
                "00-System-Instructions/{00-Reserved-Marker-Do-Not-Edit,"
                f"{PLACEHOLDER_TITLE}}}",
                "01-Whole-Page-Intake/<one complete UI-moved Page>",
            ),
            manifest_keys=keys,
            validation_conditions=(
                "the reserved marker and scaffold placeholder are unchanged",
                "the exact intake Section contains one imported root leaf Page",
                "the imported Page ID differs from the scaffold placeholder ID",
                "body revision-marker evidence includes each original author metadata value",
            ),
        )
        destination_profile = replace(
            profile,
            name="interactive-move-page-destination",
            expected_structure=(
                "01-Move-Destination/99-Destination-Anchor",
            ),
            manifest_keys=destination_keys,
            content_capabilities=("plain_text",),
            validation_conditions=(
                "the exact destination Section and anchor remain active",
            ),
        )
        UserAuthoredRecipe.__init__(
            self,
            INTERACTIVE_SCENARIO,
            notebook_roles=(
                NotebookRoleSpec(
                    "destination",
                    destination_profile,
                    {"manifest_keys": list(destination_keys)},
                ),
                NotebookRoleSpec(
                    "source",
                    role_profile,
                    {"manifest_keys": list(keys)},
                ),
            ),
            cache_recipe_name=CACHE_RECIPE_NAME,
        )

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        if context.role == "destination":
            recorder = context.recorder
            section = recorder.record_structure(
                "destination_section",
                await ensure_section(
                    context.client,
                    context.notebook_id,
                    "01-Move-Destination",
                ),
            )
            recorder.record_structure(
                "destination_anchor",
                await ensure_page(
                    context.client,
                    str(section["id"]),
                    "99-Destination-Anchor",
                    "Disposable anchor for imported whole-Page Move validation.",
                ),
            )
            return FixtureBuildResult(recorder.structure, recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(f"Unsupported whole-Page intake role: {context.role}")
        recorder = context.recorder
        instructions = recorder.record_structure(
            "source_instructions_section",
            await ensure_section(context.client, context.notebook_id, "00-System-Instructions"),
        )
        recorder.record_structure(
            "source_instructions_page",
            await ensure_page(
                context.client,
                str(instructions["id"]),
                "00-Reserved-Marker-Do-Not-Edit",
                "Whole-Page fixture intake. Do not edit this reserved marker.",
            ),
        )
        intake = recorder.record_structure(
            "source_canvas_section",
            await ensure_section(context.client, context.notebook_id, "01-Whole-Page-Intake"),
        )
        del intake
        recorder.record_structure(
            "source_canvas_page",
            await ensure_page(
                context.client,
                str(instructions["id"]),
                PLACEHOLDER_TITLE,
                "Do not edit this Page. Move or copy a complete Page into the empty intake Section.",
            ),
        )
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    @staticmethod
    def _revision_projection(snapshot: Mapping[str, Any], page_id: str) -> dict[str, Any]:
        projections = snapshot.get("page_revision_marker_projections")
        value = projections.get(page_id) if isinstance(projections, Mapping) else None
        if not isinstance(value, Mapping):
            raise InvariantFailure(
                "Imported whole Page is missing detailed revision-marker evidence."
            )
        projection = dict(value)
        markers = projection.get("markers")
        if (
            projection.get("content_exposed") is not False
            or projection.get("marker_values_exposed") is not True
            or projection.get("author_metadata_exposed") is not True
            or projection.get("sensitive_evidence") is not True
            or not isinstance(markers, list)
            or len(markers) != int(projection.get("marker_count", -1))
            or not all(
                isinstance(marker, Mapping)
                and isinstance(marker.get("value"), str)
                and isinstance(marker.get("value_sha256"), str)
                for marker in markers
            )
        ):
            raise InvariantFailure(
                "Revision-marker evidence did not include the required raw author metadata."
            )
        return projection

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        if context.role == "destination":
            section = resolved["destination_section"]
            anchor = resolved["destination_anchor"]
            checks.require(
                section.get("resource_type") == "section"
                and anchor.get("resource_type") == "page"
                and str(anchor.get("section_id", "")) == str(section["id"]),
                "Whole-Page Move destination Section or anchor is invalid.",
                "destination role exposes one exact Section and anchor",
            )
            return tuple(checks.checks)
        marker = resolved["source_instructions_page"]
        instructions = resolved["source_instructions_section"]
        intake = resolved["source_canvas_section"]
        bound_page = resolved["source_canvas_page"]
        intake_pages = [
            item
            for item in context.snapshot.get("items", ())
            if isinstance(item, Mapping)
            and item.get("resource_type") == "page"
            and str(item.get("section_id", "")) == str(intake["id"])
        ]
        checks.require(
            display_name(marker) == "00-Reserved-Marker-Do-Not-Edit",
            "Whole-Page intake reserved marker was modified.",
            "reserved marker title remains unchanged",
        )
        scaffold_mode = (
            str(bound_page.get("section_id", "")) == str(instructions["id"])
            and display_name(bound_page) == PLACEHOLDER_TITLE
        )
        imported_mode = str(bound_page.get("section_id", "")) == str(intake["id"])
        if scaffold_mode:
            checks.require(
                not intake_pages,
                "Whole-Page intake Section must be empty before the user checkpoint.",
                "intake Section starts empty",
            )
        elif imported_mode:
            checks.require(
                len(intake_pages) == 1
                and str(intake_pages[0].get("id", "")) == str(bound_page["id"])
                and int(bound_page.get("page_level", 1)) == 1
                and bound_page.get("parent_page_id") is None,
                "Whole-Page intake is not one exact imported root leaf Page.",
                "one exact imported root leaf Page occupies the intake Section",
            )
            revision = self._revision_projection(context.snapshot, str(bound_page["id"]))
            checks.require(
                int(revision.get("marker_count", 0)) > 0,
                "Imported whole Page has no body authorship/revision markers.",
                "imported Page retains body revision-marker evidence",
            )
        else:
            raise InvariantFailure(
                "Whole-Page intake binding escaped both scaffold and imported states."
            )
        return tuple(checks.checks)

    def freeze_authored_structures(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        source = observation.roles["source"]
        structure = source.build.structure
        snapshot = source.snapshot
        instructions_id = str(structure["source_instructions_section"]["id"])
        intake_id = str(structure["source_canvas_section"]["id"])
        placeholder_id = str(structure["source_canvas_page"]["id"])
        items = [item for item in snapshot.get("items", ()) if isinstance(item, Mapping)]
        placeholder = next(
            (item for item in items if str(item.get("id", "")) == placeholder_id),
            None,
        )
        if not (
            placeholder is not None
            and display_name(dict(placeholder)) == PLACEHOLDER_TITLE
            and str(placeholder.get("section_id", "")) == instructions_id
        ):
            raise InvariantFailure(
                "Whole-Page intake placeholder was edited, moved, or removed."
            )
        imported = [
            item
            for item in items
            if item.get("resource_type") == "page"
            and str(item.get("section_id", "")) == intake_id
        ]
        if len(imported) != 1:
            raise InvariantFailure(
                "Move exactly one complete Page into the intake Section; no Page was selected by name."
            )
        page = dict(imported[0])
        if (
            str(page.get("id", "")) == placeholder_id
            or int(page.get("page_level", 1)) != 1
            or page.get("parent_page_id") is not None
            or not display_name(page).strip()
        ):
            raise InvariantFailure(
                "Imported whole Page must be a new, non-empty-title root leaf Page."
            )
        revision = self._revision_projection(snapshot, str(page["id"]))
        if int(revision.get("marker_count", 0)) < 1:
            raise InvariantFailure(
                "Imported whole Page must retain at least one body authorship/revision marker."
            )
        frozen = {key: dict(value) for key, value in structure.items()}
        frozen["source_canvas_page"] = page
        destination = observation.roles["destination"]
        return {
            "source": frozen,
            "destination": {
                key: dict(value)
                for key, value in destination.build.structure.items()
            },
        }

    def authored_content_report(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, Any]:
        classification, page_id = self._classification(observation)
        observed = set(classification["observed_capability_counts"])
        representative = sorted(observed & self.representative_capabilities)
        revision = self._revision_projection(
            observation.roles["source"].snapshot,
            page_id,
        )
        schema_passed = (
            classification["invalid_object_schema_count"] == 0
            and classification["missing_projection_count"] == 0
        )
        passed = (
            schema_passed
            and classification["classification_complete"]
            and bool(representative)
            and int(revision.get("marker_count", 0)) > 0
        )
        return {
            "schema_version": 1,
            "capability": self.capability,
            "requested": {
                "whole-page-ui-transfer": 1,
                "body-revision-marker": 1,
            },
            "imported_page_id": page_id,
            "accepted_kinds": sorted(self.stable_capabilities),
            "observed": classification["observed_capability_counts"],
            "representative_capabilities": representative,
            "revision_markers": revision,
            "missing": [
                value
                for value, missing in (
                    ("representative-content-capability", not representative),
                    ("body-revision-marker", int(revision.get("marker_count", 0)) < 1),
                )
                if missing
            ],
            "unexpected": classification["unknown_capabilities"],
            "unexpected_counts": {
                kind: 1 for kind in classification["unknown_capabilities"]
            },
            "supporting": classification["supporting"],
            "object_counts": classification["object_counts"],
            "invalid_object_schema_count": classification["invalid_object_schema_count"],
            "classification_complete": classification["classification_complete"],
            "template_state_candidate": "ready" if passed else "evidence_only",
            "passed": passed,
            "marker_values_exposed": True,
            "author_metadata_exposed": True,
            "sensitive_evidence": True,
            "content_exposed": False,
        }

    def freeze_authored_instance(
        self,
        observation: FixtureBundleObservation,
    ) -> AuthoredTemplateInstance:
        classification, page_id = self._classification(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        revision = self._revision_projection(source.snapshot, page_id)
        projection = {
            "imported_page": self._semantic_page_identity(source.snapshot, page_id),
            "destination_structure": self._semantic_structure_identity(
                destination.build.structure
            ),
            "revision_markers": {
                "marker_count": revision.get("marker_count"),
                "attribute_counts": revision.get("attribute_counts"),
                "node_counts": revision.get("node_counts"),
                "sha256": revision.get("sha256"),
                "markers": revision.get("markers"),
                "marker_values_exposed": revision.get("marker_values_exposed"),
                "author_metadata_exposed": revision.get("author_metadata_exposed"),
                "sensitive_evidence": revision.get("sensitive_evidence"),
            },
        }
        payload = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        observed = set(classification["observed_capability_counts"])
        unknown = set(classification["unknown_capabilities"])
        ready = (
            classification["classification_complete"]
            and bool(observed & self.representative_capabilities)
            and int(revision.get("marker_count", 0)) > 0
        )
        return AuthoredTemplateInstance(
            template_instance_id=f"authored-{digest[:24]}",
            state="ready" if ready else "evidence_only",
            mutation_eligible=ready,
            move_source_deletion_allowed=ready,
            projection_digest=digest,
            observed_capabilities=tuple(sorted(observed)),
            unknown_capabilities=tuple(sorted(unknown)),
        )

    def validate_authored_content(
        self,
        observation: FixtureBundleObservation,
        report: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        live = self.validate_live(observation)
        report = dict(report or self.authored_content_report(observation))
        if report.get("passed") is not True:
            raise InvariantFailure(
                "Whole-Page intake requires one exact imported root leaf Page, a complete "
                "typed projection, meaningful supported content, and body revision markers."
            )
        return {
            **report,
            "authoring_zones": [zone.manifest_key for zone in self.authoring_zones],
            "role_checks": {role: list(checks) for role, checks in live.role_checks.items()},
            "bundle_checks": list(live.bundle_checks),
        }

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        return MovePageContentRecipe.validate_live(self, observation)


RECIPE = InteractiveMovePageRecipe()

__all__ = ["INTERACTIVE_SCENARIO", "InteractiveMovePageRecipe", "RECIPE"]
