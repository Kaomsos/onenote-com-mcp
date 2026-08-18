"""Human-authored two-Notebook fixture for real-content Page Move diagnosis."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping

from ...runtime import InvariantFailure
from ...test_utils import display_name
from ..common.config import VALIDATED_COPY_CAPABILITIES
from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from ..common.specs import get_scenario_spec
from .interactive import (
    AuthoredTemplateInstance,
    AuthoringZoneSpec,
    UserAuthoredRecipe,
)
from .recipe_base import (
    FixtureBundleObservation,
    FixtureValidationReport,
    NotebookRoleSpec,
)


INTERACTIVE_SCENARIO = "interactive-move-page-content"
CACHE_RECIPE_NAME = "interactive-move-page-content"


class MovePageContentRecipe(UserAuthoredRecipe):
    """Freeze one exact representative Page plus an isolated Move destination."""

    capability = "MovePageContent"
    recipe_version = 11
    requested_object_types = frozenset({"Outline"})
    stable_capabilities = frozenset(VALIDATED_COPY_CAPABILITIES)
    # Outline alone is the scaffold/placeholder shape.  RichText is a validated
    # Copy capability and is representative once the typed projection actually
    # observes embedded rich-text markup on the authored Page.
    representative_capabilities = stable_capabilities - {"Outline"}
    synthetic_content_only = False
    authoring_instruction = (
        "In the exact 01-Representative-Page Canvas, create or paste one non-sensitive "
        "representative Page using real OneNote authoring features. Include meaningful "
        "rich text or at least one supported structured capability such as a table, "
        "list/tag, image, attachment, display equation, ink, media, or UI shape. The "
        "Canvas title may be changed and "
        "will be frozen with the authored Page. Do not edit the reserved marker or add Pages."
    )
    authoring_zones = (
        AuthoringZoneSpec(
            role="source",
            manifest_key="source_canvas_page",
            allowed_operations=("add_page_content", "edit_page_content"),
        ),
    )
    bundle_invariants = (
        "source and destination Notebook IDs and resolved paths are unique",
        "the representative source is one exact leaf Page",
        "the Move destination belongs only to the destination Notebook role",
    )

    def __init__(self) -> None:
        profile = get_scenario_spec(INTERACTIVE_SCENARIO).fixture
        source_keys = (
            "source_instructions_section",
            "source_instructions_page",
            "source_canvas_section",
            "source_canvas_page",
        )
        destination_keys = (
            "destination_section",
            "destination_anchor",
        )
        source_profile = replace(
            profile,
            name="interactive-move-page-content-source",
            expected_structure=(
                "00-System-Instructions/00-Reserved-Marker-Do-Not-Edit",
                "01-Move-Source/01-Representative-Page",
            ),
            manifest_keys=source_keys,
            validation_conditions=(
                "the reserved marker is unchanged",
                "the exact representative Canvas is one active leaf Page",
            ),
        )
        destination_profile = replace(
            profile,
            name="interactive-move-page-content-destination",
            expected_structure=(
                "01-Move-Destination/99-Destination-Anchor",
            ),
            manifest_keys=destination_keys,
            content_capabilities=("plain_text",),
            validation_conditions=(
                "the exact destination Section and anchor remain active",
            ),
        )
        super().__init__(
            INTERACTIVE_SCENARIO,
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
            cache_recipe_name=CACHE_RECIPE_NAME,
        )

    def select_template_instance_id(
        self,
        args: argparse.Namespace,
        *,
        allow_unselected: bool = False,
        cache_store: Any | None = None,
    ) -> str:
        return UserAuthoredRecipe.select_template_instance_id(
            self,
            args,
            allow_unselected=allow_unselected,
            cache_store=cache_store,
        )

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        if context.role == "destination":
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
                    "Disposable anchor for representative Page Move validation.",
                ),
            )
            return FixtureBuildResult(recorder.structure, recorder.evidence)
        if context.role != "source":
            raise InvariantFailure(
                f"Unsupported interactive Move fixture role: {context.role}"
            )

        instructions = recorder.record_structure(
            "source_instructions_section",
            await ensure_section(
                context.client,
                context.notebook_id,
                "00-System-Instructions",
            ),
        )
        recorder.record_structure(
            "source_instructions_page",
            await ensure_page(
                context.client,
                str(instructions["id"]),
                "00-Reserved-Marker-Do-Not-Edit",
                "Bounded representative-content Move fixture. Do not edit this marker.",
            ),
        )
        canvas_section = recorder.record_structure(
            "source_canvas_section",
            await ensure_section(
                context.client,
                context.notebook_id,
                "01-Move-Source",
            ),
        )
        recorder.record_structure(
            "source_canvas_page",
            await ensure_page(
                context.client,
                str(canvas_section["id"]),
                "01-Representative-Page",
                "Replace this placeholder with non-sensitive representative Page content.",
            ),
        )
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(
            context.snapshot,
            build.structure,
        )
        if context.role == "destination":
            section = resolved["destination_section"]
            anchor = resolved["destination_anchor"]
            checks.require(
                section.get("resource_type") == "section"
                and anchor.get("resource_type") == "page"
                and str(anchor.get("section_id", "")) == str(section["id"]),
                "Interactive Move destination Section or anchor is invalid.",
                "destination role exposes one exact Section and anchor",
            )
            return tuple(checks.checks)

        marker = resolved["source_instructions_page"]
        canvas_section = resolved["source_canvas_section"]
        canvas = resolved["source_canvas_page"]
        page_items = [
            item
            for item in context.snapshot.get("items", ())
            if isinstance(item, Mapping)
            and item.get("resource_type") == "page"
            and str(item.get("section_id", "")) == str(canvas_section["id"])
        ]
        checks.require(
            display_name(marker) == "00-Reserved-Marker-Do-Not-Edit",
            "Interactive Move reserved marker was modified.",
            "reserved marker title remains unchanged",
        )
        checks.require(
            canvas.get("resource_type") == "page"
            and str(canvas.get("section_id", "")) == str(canvas_section["id"])
            and int(canvas.get("page_level", 1)) == 1
            and canvas.get("parent_page_id") is None
            and [str(item.get("id")) for item in page_items] == [str(canvas["id"])],
            "Representative Move Canvas is not one exact leaf Page.",
            "representative source is one exact active leaf Page",
        )
        return tuple(checks.checks)

    @staticmethod
    def _exact_page_snapshot(
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        return {
            "items": [
                dict(item)
                for item in snapshot.get("items", ())
                if isinstance(item, Mapping) and str(item.get("id", "")) == page_id
            ],
            "page_hashes": {
                page_id: snapshot.get("page_hashes", {}).get(page_id)
            },
            "page_objects": {
                page_id: list(snapshot.get("page_objects", {}).get(page_id, ()))
            },
            "page_capability_projections": {
                page_id: snapshot.get("page_capability_projections", {}).get(page_id)
            },
        }

    @staticmethod
    def _semantic_page_identity(
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        """Return the authored Page identity without copy-local IDs or paths.

        Cache materialization creates a new OneNote identity graph.  In particular,
        Page/Object IDs, Notebook IDs, and resolved paths are expected to differ even
        when the opaque working bundle is byte-for-byte derived from the published
        template.  For the reviewed rich/list/table/image tier, the identity uses
        a materialization-stable semantic projection: OneNote may reserialize
        presentation-only rich-text spans and calculated table attributes while
        opening an opaque byte-for-byte copy. The later Move read-back remains the
        strict fidelity gate. Other capability tiers retain the stricter stable
        body-hash fallback.
        """

        pages = [
            item
            for item in snapshot.get("items", ())
            if isinstance(item, Mapping)
            and str(item.get("id", "")) == page_id
            and item.get("resource_type") == "page"
        ]
        if len(pages) != 1:
            raise InvariantFailure(
                "Representative Move Canvas is not uniquely present in the authored snapshot."
            )
        body_hashes = snapshot.get("page_body_hashes")
        body_hash = (
            body_hashes.get(page_id) if isinstance(body_hashes, Mapping) else None
        )
        if not isinstance(body_hash, str) or not body_hash:
            raise InvariantFailure(
                "Representative Move Canvas is missing its stable page body hash."
            )
        semantic_identities = snapshot.get("page_semantic_content_identities")
        semantic_identity = (
            semantic_identities.get(page_id)
            if isinstance(semantic_identities, Mapping)
            else None
        )
        semantic_digest = None
        if (
            isinstance(semantic_identity, Mapping)
            and semantic_identity.get("complete") is True
        ):
            semantic_digest = semantic_identity.get("materialization_sha256")
            if not isinstance(semantic_digest, str) or not semantic_digest:
                raise InvariantFailure(
                    "Representative Move Canvas has an invalid materialization semantic digest."
                )
        projections = snapshot.get("page_capability_projections")
        projection = (
            projections.get(page_id) if isinstance(projections, Mapping) else None
        )
        objects = snapshot.get("page_objects")
        page_objects = objects.get(page_id, ()) if isinstance(objects, Mapping) else ()
        page = pages[0]
        return {
            "resource_type": page.get("resource_type"),
            "title": page.get("title"),
            "page_level": page.get("page_level"),
            "is_root_page": page.get("parent_page_id") is None,
            "content_identity": (
                {
                    "kind": "semantic_content_materialization_v2",
                    "sha256": semantic_digest,
                }
                if semantic_digest is not None
                else {
                    "kind": "stable_page_body_v1",
                    "sha256": body_hash,
                }
            ),
            "capability_projection": {
                "capabilities": sorted(
                    str(value)
                    for value in projection.get("capabilities", ())
                )
                if isinstance(projection, Mapping)
                else [],
                "unknown_nodes": sorted(
                    str(value)
                    for value in projection.get("unknown_nodes", ())
                )
                if isinstance(projection, Mapping)
                else [],
                "unsupported_page_roots": sorted(
                    str(value)
                    for value in projection.get("unsupported_page_roots", ())
                )
                if isinstance(projection, Mapping)
                else [],
                "complete": (
                    projection.get("complete") is True
                    if isinstance(projection, Mapping)
                    else False
                ),
            },
            "object_kind_identity": (
                {
                    "kind": "semantic_content_v1_owned",
                }
                if semantic_digest is not None
                else {
                    "kind": "exact_object_kind_multiset_v1",
                    "values": sorted(
                        str(value.get("kind"))
                        for value in page_objects
                        if isinstance(value, Mapping)
                    ),
                }
            ),
        }

    @staticmethod
    def _semantic_structure_identity(
        structure: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Describe declared destination topology in manifest-key, not COM-ID, terms."""

        ids_to_keys = {
            str(value["id"]): key
            for key, value in structure.items()
            if isinstance(value, Mapping) and value.get("id")
        }

        def parent_key(value: Mapping[str, Any]) -> str | None:
            for field in ("parent_page_id", "section_id", "parent_id"):
                parent_id = value.get(field)
                if parent_id is not None:
                    return ids_to_keys.get(str(parent_id), "notebook_or_external_root")
            return None

        return {
            key: {
                "resource_type": value.get("resource_type"),
                "name": value.get("title", value.get("name")),
                "page_level": value.get("page_level"),
                "parent_key": parent_key(value),
            }
            for key, value in sorted(structure.items())
            if isinstance(value, Mapping)
        }

    def _classification(
        self,
        observation: FixtureBundleObservation,
    ) -> tuple[dict[str, Any], str]:
        source = observation.roles["source"]
        page_id = str(source.build.structure["source_canvas_page"]["id"])
        exact = self._exact_page_snapshot(source.snapshot, page_id)
        return super()._classify_authored_snapshot(exact), page_id

    def authored_content_report(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, Any]:
        classification, page_id = self._classification(observation)
        observed = set(classification["observed_capability_counts"])
        representative = sorted(observed & self.representative_capabilities)
        schema_passed = (
            classification["invalid_object_schema_count"] == 0
            and classification["missing_projection_count"] == 0
        )
        return {
            "schema_version": 1,
            "capability": self.capability,
            "requested": {"exact-representative-leaf-page": 1},
            "canvas_page_id": page_id,
            "accepted_kinds": sorted(self.stable_capabilities),
            "observed": classification["observed_capability_counts"],
            "representative_capabilities": representative,
            "missing": ([] if representative else ["representative-content-capability"]),
            "unexpected": classification["unknown_capabilities"],
            "unexpected_counts": {
                kind: 1 for kind in classification["unknown_capabilities"]
            },
            "supporting": classification["supporting"],
            "object_counts": classification["object_counts"],
            "invalid_object_schema_count": classification[
                "invalid_object_schema_count"
            ],
            "classification_complete": classification["classification_complete"],
            "template_state_candidate": (
                "ready"
                if classification["classification_complete"] and representative
                else "evidence_only"
            ),
            "passed": (
                schema_passed
                and classification["classification_complete"]
                and bool(representative)
            ),
        }

    def freeze_authored_structures(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Freeze the live Canvas title/path after the user authors representative content."""

        source = observation.roles["source"]
        resolved, _by_id, _checks = resolve_active_structure(
            source.snapshot,
            source.build.structure,
        )
        canvas = resolved["source_canvas_page"]
        if not isinstance(canvas.get("title"), str) or not canvas["title"].strip():
            raise InvariantFailure(
                "Representative Move Canvas must retain a non-empty authored Page title."
            )
        frozen_source = {
            key: dict(value) for key, value in source.build.structure.items()
        }
        frozen_source["source_canvas_page"] = dict(canvas)
        destination = observation.roles["destination"]
        return {
            "source": frozen_source,
            "destination": {
                key: dict(value)
                for key, value in destination.build.structure.items()
            },
        }

    def freeze_authored_instance(
        self,
        observation: FixtureBundleObservation,
    ) -> AuthoredTemplateInstance:
        classification, page_id = self._classification(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        projection = {
            "source_page": self._semantic_page_identity(source.snapshot, page_id),
            "destination_structure": self._semantic_structure_identity(
                destination.build.structure
            ),
        }
        payload = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        observed = set(classification["observed_capability_counts"])
        representative = observed & self.representative_capabilities
        unknown = set(classification["unknown_capabilities"])
        ready = classification["classification_complete"] and bool(representative)
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
                "Representative Move content requires one exact leaf Page, a complete "
                "typed projection, and meaningful RichText or another supported "
                "structured capability."
            )
        return {
            **report,
            "authoring_zones": [zone.manifest_key for zone in self.authoring_zones],
            "role_checks": {
                role: list(checks) for role, checks in live.role_checks.items()
            },
            "bundle_checks": list(live.bundle_checks),
        }

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport:
        report = super().validate_live(observation)
        source = observation.roles["source"]
        destination = observation.roles["destination"]
        destination_section = destination.build.structure["destination_section"]
        if str(destination_section.get("parent_id", "")) != str(
            destination.notebook.get("id", "")
        ):
            raise InvariantFailure(
                "Interactive Move destination Section escaped its Notebook role."
            )
        if str(source.notebook.get("id", "")) == str(
            destination.notebook.get("id", "")
        ):
            raise InvariantFailure(
                "Interactive Move source and destination resolved to the same Notebook."
            )
        return FixtureValidationReport(
            passed=report.passed,
            role_checks=report.role_checks,
            bundle_checks=report.bundle_checks
            + ("cross-Notebook destination is bound to the destination role",),
        )


RECIPE = MovePageContentRecipe()

__all__ = [
    "INTERACTIVE_SCENARIO",
    "MovePageContentRecipe",
    "RECIPE",
]
