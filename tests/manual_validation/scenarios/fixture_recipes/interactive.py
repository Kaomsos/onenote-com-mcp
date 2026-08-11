"""Explicit human-bootstrap Recipe types for otherwise uncreatable fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from ...runtime import InvariantFailure, RunnerFailure
from ..common.fixture_builders import ensure_page, ensure_section
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
    resolve_active_structure,
)
from .recipe_base import BuildMode, FixtureBundleObservation, RecipeBase


class InteractiveBootstrapRequired(RunnerFailure):
    pass


@dataclass(frozen=True)
class AuthoringZoneSpec:
    role: str
    manifest_key: str
    allowed_operations: tuple[str, ...]


@dataclass(frozen=True)
class AuthoredTemplateInstance:
    template_instance_id: str
    state: str
    mutation_eligible: bool
    move_source_deletion_allowed: bool
    projection_digest: str
    observed_capabilities: tuple[str, ...]
    unknown_capabilities: tuple[str, ...]


class InteractiveFixtureRecipe(RecipeBase):
    """Base for one exact UI-created content capability; never auto-bootstraps."""

    build_mode = BuildMode.HUMAN_BOOTSTRAP_REQUIRED
    bootstrap_scenario_name = ""
    capability = ""
    canvas_title = "01-Interactive-Canvas"
    requested_object_types: frozenset[str] = frozenset()
    expected_object_count = 1
    authoring_instruction = "Add exactly one requested synthetic content object."
    consumer_scenario = False
    recipe_version = 3
    supporting_object_kinds = frozenset({"Outline", "OE"})

    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        raise InteractiveBootstrapRequired(
            f"interactive_bootstrap_required: {self.bootstrap_scenario_name}"
        )

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        section = context.recorder.record_structure(
            "canvas_section",
            await ensure_section(
                context.client,
                context.notebook_id,
                f"00-{self.capability}-Canvas",
            ),
        )
        page = context.recorder.record_structure(
            "canvas_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                self.canvas_title,
                (
                    "Synthetic manual-validation content only. "
                    f"{self.authoring_instruction}"
                ),
            ),
        )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate_registration(self, spec) -> None:
        super().validate_registration(spec)
        if not self.bootstrap_scenario_name or (
            self.bootstrap_scenario_name != self.scenario_name and not self.consumer_scenario
        ):
            raise ValueError("Interactive Recipe must bind a fixed bootstrap Scenario.")
        if not self.capability or not self.requested_object_types:
            raise ValueError("Interactive Recipe must declare one exact capability detector.")

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(
            resolved["canvas_page"].get("section_id") == resolved["canvas_section"].get("id"),
            "Interactive Canvas Page is outside its exact Section.",
            "interactive Canvas Page remains under its exact Section",
        )
        return tuple(checks.checks)

    def detect_authored_content(
        self,
        snapshot: Mapping[str, Any],
        canvas_page_id: str,
    ) -> tuple[dict[str, Any], ...]:
        objects = snapshot.get("page_objects", {}).get(canvas_page_id, [])
        return tuple(
            dict(value)
            for value in objects
            if isinstance(value, dict) and value.get("kind") in self.requested_object_types
        )

    def authored_content_report(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, Any]:
        role = observation.roles["source"]
        page_id = str(role.build.structure["canvas_page"]["id"])
        objects = tuple(role.snapshot.get("page_objects", {}).get(page_id, ()))
        invalid_schema = sum(
            not isinstance(value, dict)
            or not isinstance(value.get("kind"), str)
            or not value.get("kind")
            or "type" in value
            for value in objects
        )
        object_counts = Counter(
            str(value["kind"])
            for value in objects
            if isinstance(value, dict)
            and isinstance(value.get("kind"), str)
            and value.get("kind")
            and "type" not in value
        )
        observed = {
            kind: object_counts[kind]
            for kind in sorted(self.requested_object_types)
            if object_counts[kind]
        }
        supporting = {
            kind: object_counts[kind]
            for kind in sorted(self.supporting_object_kinds)
            if object_counts[kind]
        }
        unexpected_counts = {
            kind: count
            for kind, count in sorted(object_counts.items())
            if kind not in self.requested_object_types
            and kind not in self.supporting_object_kinds
        }
        projections = role.snapshot.get("page_capability_projections", {})
        projection = projections.get(page_id) if isinstance(projections, dict) else None
        projection_error = not isinstance(projection, dict)
        unknown_nodes = [] if projection_error else list(projection.get("unknown_nodes", ()))
        unsupported_roots = (
            [] if projection_error else list(projection.get("unsupported_page_roots", ()))
        )
        projected_capabilities = (
            set() if projection_error else set(projection.get("capabilities", ()))
        )
        unexpected_capabilities = sorted(
            projected_capabilities - self.requested_object_types - {"Outline"}
        )
        observed_total = sum(observed.values())
        missing = [self.capability] if observed_total < self.expected_object_count else []
        unexpected = sorted(
            set(unexpected_counts)
            | set(unexpected_capabilities)
            | ({"invalid-object-schema"} if invalid_schema else set())
            | ({"missing-capability-projection"} if projection_error else set())
            | {f"unknown-node:{value}" for value in unknown_nodes}
            | {f"unsupported-root:{value}" for value in unsupported_roots}
        )
        passed = (
            observed_total == self.expected_object_count
            and not missing
            and not unexpected
            and self.capability in projected_capabilities
            and projection.get("complete") is True
        )
        representation_status = "requested_kind_observed" if passed else "mismatch"
        return {
            "schema_version": 3,
            "capability": self.capability,
            "requested": {self.capability: self.expected_object_count},
            "accepted_kinds": sorted(self.requested_object_types),
            "observed": observed,
            "missing": missing,
            "unexpected": unexpected,
            "unexpected_counts": unexpected_counts,
            "supporting": supporting,
            "object_counts": {
                kind: object_counts[kind] for kind in sorted(object_counts)
            },
            "invalid_object_schema_count": invalid_schema,
            "capability_projection": projection,
            "representation_status": representation_status,
            "template_publish_allowed": passed,
            "passed": passed,
        }

    def validate_authored_content(
        self,
        observation: FixtureBundleObservation,
        report: Mapping[str, Any] | None = None,
    ):
        report = dict(report or self.authored_content_report(observation))
        if report.get("passed") is not True:
            observed_total = sum(int(value) for value in report.get("observed", {}).values())
            unexpected = ", ".join(
                f"{kind}={report.get('unexpected_counts', {}).get(kind, 1)}"
                for kind in report.get("unexpected", ())
            ) or "none"
            summary = (
                f"requested {self.capability}={self.expected_object_count}; "
                f"observed={observed_total}; "
                f"missing={','.join(report.get('missing', ())) or 'none'}; "
                f"unexpected={unexpected}"
            )
            raise InvariantFailure(f"Interactive detector mismatch: {summary}.")
        return report

    def compare_capability(
        self,
        source_objects: tuple[Mapping[str, Any], ...],
        target_objects: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        source_types = sorted(str(value.get("kind")) for value in source_objects)
        target_types = sorted(str(value.get("kind")) for value in target_objects)
        valid_schema = all(
            isinstance(value.get("kind"), str) and "type" not in value
            for value in (*source_objects, *target_objects)
        )
        passed = (
            valid_schema
            and source_types == target_types
            and len(source_types) == self.expected_object_count
            and set(source_types).issubset(self.requested_object_types)
        )
        return {
            "capability": self.capability,
            "equivalent": passed,
            "checks": {"object_type_and_count": passed},
        }


class InsertedFileInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "InsertedFile"
    requested_object_types = frozenset({"InsertedFile"})
    authoring_instruction = (
        "Drag exactly one synthetic local file onto this Canvas and keep the inserted-file "
        "representation; do not add a second object."
    )


class InkDrawingInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "InkDrawing"
    requested_object_types = frozenset({"InkDrawing"})
    authoring_instruction = "Draw exactly one small synthetic ink stroke on this Canvas."


class MediaFileInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "MediaFile"
    requested_object_types = frozenset({"MediaFile"})
    authoring_instruction = (
        "Insert exactly one disposable synthetic audio or video object on this Canvas."
    )


class UserAuthoredRecipe(InteractiveFixtureRecipe):
    """Freeze a bounded disposable authoring zone into an explicitly selected instance."""

    capability = "UserAuthored"
    requested_object_types = frozenset({"Outline"})
    authoring_instruction = (
        "Create only synthetic content inside the declared authoring zone and do not edit "
        "the reserved system marker."
    )
    authoring_zones = (
        AuthoringZoneSpec(
            role="source",
            manifest_key="authoring_zone_section",
            allowed_operations=("create", "delete", "rename", "reorder", "add_page_content"),
        ),
    )
    stable_capabilities = frozenset(
        {"Outline", "Image", "RichText", "Table", "List", "Tag"}
    )
    requires_instance_selection = True

    def select_template_instance_id(
        self,
        args: argparse.Namespace,
        *,
        allow_unselected: bool = False,
    ) -> str:
        value = str(getattr(args, "template_instance_id", "") or "")
        if not value:
            if allow_unselected:
                return "required-explicit-template-instance"
            raise RunnerFailure(
                "UserAuthoredRecipe requires an explicit --template-instance-id; "
                "selection is never inferred."
            )
        if not value.startswith("authored-") or len(value) != 33:
            raise RunnerFailure("User-authored template instance ID has an invalid typed format.")
        return value

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        instructions = context.recorder.record_structure(
            "instructions_section",
            await ensure_section(context.client, context.notebook_id, "00-System-Instructions"),
        )
        context.recorder.record_structure(
            "instructions_page",
            await ensure_page(
                context.client,
                str(instructions["id"]),
                "00-Reserved-Marker-Do-Not-Edit",
                "Synthetic authoring zone validation. Do not edit this reserved marker.",
            ),
        )
        zone = context.recorder.record_structure(
            "authoring_zone_section",
            await ensure_section(context.client, context.notebook_id, "01-Authoring-Zone"),
        )
        context.recorder.record_structure(
            "authoring_zone_page",
            await ensure_page(
                context.client,
                str(zone["id"]),
                "01-Author-Here",
                "Create only synthetic content inside this authoring zone.",
            ),
        )
        return FixtureBuildResult(context.recorder.structure, context.recorder.evidence)

    def validate(self, context, build) -> tuple[str, ...]:
        resolved, _by_id, checks = resolve_active_structure(context.snapshot, build.structure)
        checks.require(
            resolved["instructions_page"].get("title") == "00-Reserved-Marker-Do-Not-Edit",
            "User-authored system marker was modified.",
            "reserved authoring marker is unchanged",
        )
        return tuple(checks.checks)

    def _classify_authored_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        page_ids = {
            str(item.get("id"))
            for item in snapshot.get("items", ())
            if isinstance(item, dict)
            and item.get("resource_type") == "page"
            and item.get("id")
        }
        projections = snapshot.get("page_capability_projections")
        projection_map = projections if isinstance(projections, dict) else {}
        missing_projections = sorted(page_ids - set(projection_map))
        observed = Counter()
        unknown: set[str] = set()
        projection_complete = not missing_projections
        for page_id in sorted(page_ids):
            projection = projection_map.get(page_id)
            if not isinstance(projection, dict):
                continue
            for capability in projection.get("capabilities", ()):
                observed[str(capability)] += 1
            unknown.update(
                f"unknown-node:{value}" for value in projection.get("unknown_nodes", ())
            )
            unknown.update(
                f"unsupported-root:{value}"
                for value in projection.get("unsupported_page_roots", ())
            )
            if projection.get("complete") is not True:
                projection_complete = False
        unknown.update(set(observed) - self.stable_capabilities)
        objects = [
            value
            for values in snapshot.get("page_objects", {}).values()
            if isinstance(values, list)
            for value in values
        ]
        invalid_object_schema_count = sum(
            not isinstance(value, dict)
            or not isinstance(value.get("kind"), str)
            or not value.get("kind")
            or "type" in value
            for value in objects
        )
        object_counts = Counter(
            str(value["kind"])
            for value in objects
            if isinstance(value, dict)
            and isinstance(value.get("kind"), str)
            and value.get("kind")
            and "type" not in value
        )
        if invalid_object_schema_count:
            unknown.add("invalid-object-schema")
        if missing_projections:
            unknown.add("missing-capability-projection")
        return {
            "observed_capability_counts": {
                kind: observed[kind] for kind in sorted(observed)
            },
            "unknown_capabilities": sorted(unknown),
            "object_counts": {
                kind: object_counts[kind] for kind in sorted(object_counts)
            },
            "supporting": {
                kind: object_counts[kind]
                for kind in sorted(self.supporting_object_kinds)
                if object_counts[kind]
            },
            "invalid_object_schema_count": invalid_object_schema_count,
            "missing_projection_count": len(missing_projections),
            "projection_complete": projection_complete,
            "classification_complete": (
                projection_complete
                and not unknown
                and invalid_object_schema_count == 0
            ),
        }

    def authored_content_report(
        self,
        observation: FixtureBundleObservation,
    ) -> dict[str, Any]:
        classification = self._classify_authored_snapshot(
            observation.roles["source"].snapshot
        )
        schema_passed = (
            classification["invalid_object_schema_count"] == 0
            and classification["missing_projection_count"] == 0
        )
        return {
            "schema_version": 1,
            "capability": self.capability,
            "requested": {"bounded-authoring-zone": 1},
            "accepted_kinds": sorted(self.stable_capabilities),
            "observed": classification["observed_capability_counts"],
            "missing": [],
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
            "passed": schema_passed,
        }

    def freeze_authored_instance(
        self,
        observation: FixtureBundleObservation,
    ) -> AuthoredTemplateInstance:
        role = observation.roles["source"]
        snapshot = role.snapshot
        projection = {
            "items": sorted(
                (
                    {key: value for key, value in item.items() if key != "modified"}
                    for item in snapshot.get("items", [])
                ),
                key=lambda value: str(value.get("id", "")),
            ),
            "page_hashes": snapshot.get("page_hashes", {}),
            "page_objects": snapshot.get("page_objects", {}),
            "page_capability_projections": snapshot.get(
                "page_capability_projections", {}
            ),
        }
        payload = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        classification = self._classify_authored_snapshot(snapshot)
        observed = set(classification["observed_capability_counts"])
        unknown = set(classification["unknown_capabilities"])
        ready = classification["classification_complete"]
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
    ):
        role = observation.roles["source"]
        checks = self.validate(
            FixtureValidationContext(args=role.args, snapshot=role.snapshot),
            role.build,
        )
        report = dict(report or self.authored_content_report(observation))
        if report.get("passed") is not True:
            raise InvariantFailure(
                "User-authored detector received an invalid object schema or missing "
                "capability projection."
            )
        return {
            **report,
            "authoring_zones": [zone.manifest_key for zone in self.authoring_zones],
            "checks": list(checks),
        }


__all__ = [
    "AuthoredTemplateInstance",
    "AuthoringZoneSpec",
    "InkDrawingInteractiveFixtureRecipe",
    "InsertedFileInteractiveFixtureRecipe",
    "InteractiveBootstrapRequired",
    "InteractiveFixtureRecipe",
    "MediaFileInteractiveFixtureRecipe",
    "UserAuthoredRecipe",
]
