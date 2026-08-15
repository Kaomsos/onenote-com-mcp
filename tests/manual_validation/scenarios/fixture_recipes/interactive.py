"""Explicit human-bootstrap Recipe types for otherwise uncreatable fixtures."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping

from local_onenote_mcp.page.copying import (
    GENERATED_OBJECT_ATTRIBUTES,
    MATHML_NAMESPACE,
    VOLATILE_ATTRIBUTES,
    semantic_mathml_comparison,
)
from local_onenote_mcp.page.parser import local_name, parse_xml

from ...runtime import InvariantFailure, RunnerFailure
from ...test_utils import display_name, mathml_structure_projection
from ..common.config import ROOT_PAGE_COPY_CAPABILITIES
from ..common.fixture_builders import (
    ensure_copy_rich_fixture,
    ensure_page,
    ensure_section,
)
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
    requested_projected_capabilities: frozenset[str] = frozenset()
    required_plan_capabilities: frozenset[str] | None = None
    expected_object_count = 1
    authoring_instruction = "Add exactly one requested synthetic content object."
    consumer_scenario = False
    recipe_version = 3
    supporting_object_kinds = frozenset({"Outline", "OE"})
    representation_discovery_only = False
    successful_representation_status = "requested_kind_observed"
    copy_issue_code = "content_type_unverified"
    copy_issue_action = "preserved_unverified"
    copy_ui_acceptance_instruction = "every target is UI equivalent"

    def copy_target_content_report(
        self,
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        """Validate a Copy target; lossless recipes reuse their source detector."""

        return self.content_report(snapshot, page_id)

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
        if not self.capability or (
            not self.requested_object_types
            and not self.requested_projected_capabilities
            and not self.representation_discovery_only
        ):
            raise ValueError(
                "Interactive Recipe must declare one exact capability detector unless it is "
                "an evidence-only representation discovery."
            )

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
        return self.content_report(role.snapshot, page_id)

    def content_report(
        self,
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        """Classify one exact Page without accepting parser-private schema aliases."""

        objects = tuple(snapshot.get("page_objects", {}).get(page_id, ()))
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
        projections = snapshot.get("page_capability_projections", {})
        projection = projections.get(page_id) if isinstance(projections, dict) else None
        projection_error = not isinstance(projection, dict)
        unknown_nodes = [] if projection_error else list(projection.get("unknown_nodes", ()))
        unsupported_roots = (
            [] if projection_error else list(projection.get("unsupported_page_roots", ()))
        )
        projected_capabilities = (
            set() if projection_error else set(projection.get("capabilities", ()))
        )
        accepted_projected_capabilities = (
            self.requested_projected_capabilities or self.requested_object_types
        )
        unexpected_capabilities = sorted(
            projected_capabilities - accepted_projected_capabilities - {"Outline"}
        )
        observed_total = sum(observed.values())
        missing = (
            [self.capability]
            if observed_total < self.expected_object_count
            or self.capability not in projected_capabilities
            else []
        )
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
        representation_status = self.successful_representation_status if passed else "mismatch"
        return {
            "schema_version": 3,
            "capability": self.capability,
            "requested": {self.capability: self.expected_object_count},
            "accepted_kinds": sorted(self.requested_object_types),
            "accepted_projected_capabilities": sorted(
                accepted_projected_capabilities
            ),
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
            representation_gates = report.get("representation_gates")
            if isinstance(representation_gates, Mapping):
                failed_gates = sorted(
                    str(name)
                    for name, passed in representation_gates.items()
                    if passed is not True
                )
                if failed_gates:
                    summary += f"; failed_gates={','.join(failed_gates)}"
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
        type_and_count = (
            valid_schema
            and source_types == target_types
            and len(source_types) == self.expected_object_count
            and set(source_types).issubset(self.requested_object_types)
        )

        def stable_signature(value: Mapping[str, Any]) -> dict[str, Any]:
            return {
                field: value.get(field)
                for field in ("kind", "media_type", "can_delete")
                if field in value
            }

        source_signatures = sorted(
            (stable_signature(value) for value in source_objects),
            key=lambda value: json.dumps(value, sort_keys=True),
        )
        target_signatures = sorted(
            (stable_signature(value) for value in target_objects),
            key=lambda value: json.dumps(value, sort_keys=True),
        )
        stable_object_signature = source_signatures == target_signatures
        passed = type_and_count and stable_object_signature
        return {
            "capability": self.capability,
            "equivalent": passed,
            "checks": {
                "public_kind_schema": valid_schema,
                "object_type_and_count": type_and_count,
                "stable_object_signature": stable_object_signature,
            },
            "source_signatures": source_signatures,
            "target_signatures": target_signatures,
        }

    def compare_copy_readback(
        self,
        source_xml: str,
        target_xml: str,
        copy_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the static verification tier owned by this concrete Recipe."""

        del source_xml, target_xml
        page_results = copy_report.get("page_results", ())
        page_result = page_results[0] if len(page_results) == 1 else {}
        equivalence = page_result.get("equivalence", {}) if isinstance(page_result, dict) else {}
        checks = equivalence.get("checks", {}) if isinstance(equivalence, dict) else {}
        required = {
            "canonical_xml": checks.get("canonical_xml") is True,
            "content_objects": checks.get("content_objects") is True,
            "binary_sha256": checks.get("binary_sha256") is True,
            "visible_text": checks.get("visible_text") is True,
        }
        return {
            "verification_tier": "strict_canonical",
            "checks": required,
            "canonical_xml_observed": checks.get("canonical_xml") is True,
            "passed": equivalence.get("equivalent") is True and all(required.values()),
        }


class InsertedFileInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "InsertedFile"
    requested_object_types = frozenset({"InsertedFile"})
    authoring_instruction = (
        "Drag exactly one synthetic local file onto this Canvas and keep the inserted-file "
        "representation; do not add a second object."
    )
    copy_ui_acceptance_instruction = (
        "the copied attachment is visible and opens to the same synthetic file content"
    )


class DisplayEquationInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    """One programmatically generated display equation on the Source Parent base."""

    capability = "DisplayEquation"
    canvas_title = "01-Source-Parent"
    projected_capabilities = frozenset(
        {*ROOT_PAGE_COPY_CAPABILITIES, "DisplayEquation"}
    )
    requested_projected_capabilities = projected_capabilities
    required_plan_capabilities = projected_capabilities
    recipe_version = 3
    authoring_instruction = (
        "Do not edit the prepared Source Parent. Confirm that its automatically generated "
        "standalone single-line equation is visible without an extra blank formula line."
    )
    copy_ui_acceptance_instruction = (
        "the standalone equation and the prepared rich text, table, and image are visually "
        "equivalent, with no extra blank formula line"
    )
    allowed_object_kinds = frozenset(
        {"Outline", "OE", "Image", "Table", "Row", "Cell"}
    )

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        section = recorder.record_structure(
            "canvas_section",
            await ensure_section(context.client, context.notebook_id, "Source"),
        )
        page = recorder.record_structure(
            "canvas_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                self.canvas_title,
                f"Synthetic manual-validation Source Parent. {self.authoring_instruction}",
            ),
        )
        page, copy_fixture = await ensure_copy_rich_fixture(
            context.client,
            page,
            context.options.run_dir,
            include_equations=False,
        )
        appended = await context.client.call_tool(
            "append_page_content",
            {
                "page_id": str(page["id"]),
                "content": (
                    f'<p><math xmlns="{MATHML_NAMESPACE}" display="block"><mrow>'
                    "<mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn>"
                    "</msup></mrow></math></p>"
                ),
                "content_format": "html",
                "expected_title": display_name(page),
                "expected_section_id": str(section["id"]),
                "expected_modified": page.get("modified"),
                "x": 36.0,
                "y": 360.0,
            },
        )
        page = dict(appended["item"])
        final_xml = str(
            (
                await context.client.call_tool(
                    "get_page_xml",
                    {"page_id": str(page["id"]), "page_info": "all"},
                )
            )["xml"]
        )
        automated_content = list(copy_fixture.get("automated_content", ()))
        automated_content.append("display_equation")
        copy_fixture["automated_content"] = automated_content
        copy_fixture["display_equation_structure"] = mathml_structure_projection(
            final_xml
        )
        recorder.refresh_structure("canvas_page", page)
        recorder.record_evidence("copy_fixture", copy_fixture)
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def content_report(
        self,
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        objects = tuple(snapshot.get("page_objects", {}).get(page_id, ()))
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
        unexpected_object_kinds = sorted(
            kind for kind in object_counts if kind not in self.allowed_object_kinds
        )

        projections = snapshot.get("page_capability_projections", {})
        projection = projections.get(page_id) if isinstance(projections, dict) else None
        projection_error = not isinstance(projection, dict)
        projected = (
            set() if projection_error else set(projection.get("capabilities", ()))
        )
        missing_base = sorted(set(self.projected_capabilities) - projected)
        unexpected_capabilities = sorted(projected - set(self.projected_capabilities))
        unknown_nodes = [] if projection_error else list(projection.get("unknown_nodes", ()))
        unsupported_roots = (
            [] if projection_error else list(projection.get("unsupported_page_roots", ()))
        )

        math_projections = snapshot.get("page_mathml_structure_projections", {})
        math = math_projections.get(page_id) if isinstance(math_projections, dict) else None
        math_error = not isinstance(math, dict)
        display_attribute_count = (
            0 if math_error else int(math.get("display_attribute_equation_count", 0))
        )
        equation_count = (
            0
            if math_error
            else int(math.get("semantic_mathml", {}).get("equation_count", 0))
        )
        formula_valid = (
            not math_error
            and math.get("complete") is True
            and equation_count == 1
            and display_attribute_count == 1
            and int(math.get("candidate_text_node_count", 0)) == 1
            and int(math.get("standalone_candidate_count", 0)) == 1
        )
        missing = [] if formula_valid else [self.capability]
        unexpected = sorted(
            {f"object-kind:{kind}" for kind in unexpected_object_kinds}
            | {f"capability:{kind}" for kind in unexpected_capabilities}
            | {f"missing-base:{kind}" for kind in missing_base}
            | ({"invalid-object-schema"} if invalid_schema else set())
            | ({"missing-capability-projection"} if projection_error else set())
            | ({"missing-mathml-structure-projection"} if math_error else set())
            | (
                {f"mathml-equations:{equation_count}"}
                if not math_error and equation_count != 1
                else set()
            )
            | (
                {"formula-t-has-visible-residual-text"}
                if not math_error
                and equation_count == 1
                and int(math.get("standalone_candidate_count", 0)) != 1
                else set()
            )
            | {f"unknown-node:{value}" for value in unknown_nodes}
            | {f"unsupported-root:{value}" for value in unsupported_roots}
        )
        passed = (
            formula_valid
            and not missing
            and not unexpected
            and not missing_base
            and not unexpected_capabilities
            and not projection_error
            and projection.get("complete") is True
        )
        return {
            "schema_version": 1,
            "capability": self.capability,
            "requested": {self.capability: 1},
            "accepted_kinds": sorted(self.allowed_object_kinds),
            "accepted_projected_capabilities": sorted(self.projected_capabilities),
            "observed": ({self.capability: 1} if formula_valid else {}),
            "missing": missing,
            "unexpected": unexpected,
            "unexpected_counts": {
                kind: object_counts[kind] for kind in unexpected_object_kinds
            },
            "supporting": {
                kind: object_counts[kind] for kind in sorted(object_counts)
            },
            "object_counts": {
                kind: object_counts[kind] for kind in sorted(object_counts)
            },
            "invalid_object_schema_count": invalid_schema,
            "capability_projection": projection,
            "mathml_structure_projection": math,
            "display_attribute_observed": display_attribute_count == 1,
            "representation_status": "display_mathml_observed" if passed else "mismatch",
            "template_publish_allowed": passed,
            "passed": passed,
        }

    def detect_authored_content(
        self,
        snapshot: Mapping[str, Any],
        canvas_page_id: str,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(value)
            for value in snapshot.get("page_objects", {}).get(canvas_page_id, ())
            if isinstance(value, dict)
            and value.get("kind") in self.allowed_object_kinds
            and "type" not in value
        )

    def compare_capability(
        self,
        source_objects: tuple[Mapping[str, Any], ...],
        target_objects: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        valid_schema = all(
            isinstance(value.get("kind"), str)
            and "type" not in value
            and value.get("kind") in self.allowed_object_kinds
            for value in (*source_objects, *target_objects)
        )

        def signatures(values: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
            return sorted(
                (
                    {
                        field: value.get(field)
                        for field in ("kind", "media_type", "can_delete")
                        if field in value
                    }
                    for value in values
                ),
                key=lambda value: json.dumps(value, sort_keys=True),
            )

        source_signatures = signatures(source_objects)
        target_signatures = signatures(target_objects)
        equivalent = valid_schema and source_signatures == target_signatures
        return {
            "capability": self.capability,
            "equivalent": equivalent,
            "checks": {
                "public_kind_schema": valid_schema,
                "base_object_type_and_count": source_signatures == target_signatures,
                "stable_object_signature": source_signatures == target_signatures,
            },
            "source_signatures": source_signatures,
            "target_signatures": target_signatures,
        }

    def compare_copy_readback(
        self,
        source_xml: str,
        target_xml: str,
        copy_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        page_results = copy_report.get("page_results", ())
        page_result = page_results[0] if len(page_results) == 1 else {}
        equivalence = page_result.get("equivalence", {}) if isinstance(page_result, dict) else {}
        checks = equivalence.get("checks", {}) if isinstance(equivalence, dict) else {}
        semantic = semantic_mathml_comparison(source_xml, target_xml)
        source_structure = mathml_structure_projection(source_xml)
        target_structure = mathml_structure_projection(target_xml)
        source_candidates = source_structure.get("candidates", ())
        target_candidates = target_structure.get("candidates", ())
        source_candidate = source_candidates[0] if len(source_candidates) == 1 else {}
        target_candidate = target_candidates[0] if len(target_candidates) == 1 else {}
        source_break_count = int(source_candidate.get("oe_direct_t_break_count", 0))
        target_break_count = int(target_candidate.get("oe_direct_t_break_count", 0))
        def bounded_display_wrapper(candidate: Mapping[str, Any]) -> bool:
            break_count = int(candidate.get("oe_direct_t_break_count", 0))
            residual_markup = candidate.get("residual_markup_tags")
            return (
                break_count == 0 and not residual_markup
            ) or (
                break_count == 1
                and candidate.get("known_onenote_display_break_wrapper") is True
                and residual_markup == {"br": 1, "span": 1}
            )

        bounded_com_shape = bounded_display_wrapper(
            source_candidate
        ) and bounded_display_wrapper(target_candidate)
        required = {
            "visible_text": checks.get("visible_text") is True,
            "content_objects": checks.get("content_objects") is True,
            "binary_sha256": checks.get("binary_sha256") is True,
            "semantic_mathml": checks.get("semantic_mathml") is True,
            "direct_mathml_projection": (
                semantic.get("source_complete") is True
                and semantic.get("target_complete") is True
                and semantic.get("source_equation_count") == 1
                and semantic.get("target_equation_count") == 1
                and semantic.get("projection_equal") is True
            ),
            "one_standalone_equation_each": all(
                projection.get("complete") is True
                and projection.get("semantic_mathml", {}).get("equation_count") == 1
                and projection.get("candidate_text_node_count") == 1
                and projection.get("standalone_candidate_count") == 1
                for projection in (source_structure, target_structure)
            ),
        }
        if self.capability == "DisplayEquation":
            required.update(
                {
                    "display_equation_com_normalization": (
                        checks.get("display_equation_com_normalization") is True
                    ),
                    "outside_mathml_canonical_after_display_normalization": (
                        checks.get("outside_mathml_canonical") is True
                    ),
                    "bounded_zero_or_one_empty_wrapper": bounded_com_shape,
                }
            )
        else:
            required["outside_mathml_canonical"] = (
                checks.get("outside_mathml_canonical") is True
            )
        return {
            "verification_tier": (
                "semantic_display_equation"
                if self.capability == "DisplayEquation"
                else "semantic_mathml"
            ),
            "checks": required,
            "semantic_mathml_comparison": semantic,
            "source_mathml_structure": source_structure,
            "target_mathml_structure": target_structure,
            "display_break_observation": {
                "source_count": source_break_count,
                "target_count": target_break_count,
                "delta": target_break_count - source_break_count,
                "target_matches_recorded_span_br_shape": target_candidate.get(
                    "known_onenote_display_break_wrapper"
                )
                is True,
            },
            "temporary_known_com_normalization_accepted": False,
            "documented_display_equation_com_normalization_accepted": (
                checks.get("display_equation_com_normalization") is True
                and bounded_com_shape
            ),
            "exact_structure_equal": source_structure == target_structure,
            "passed": equivalence.get("equivalent") is True
            and all(required.values()),
        }


class InlineEquationInteractiveFixtureRecipe(DisplayEquationInteractiveFixtureRecipe):
    """One programmatically generated inline equation on the Source Parent base."""

    capability = "InlineEquation"
    projected_capabilities = frozenset(ROOT_PAGE_COPY_CAPABILITIES)
    requested_projected_capabilities = projected_capabilities
    required_plan_capabilities = projected_capabilities
    recipe_version = 2
    authoring_instruction = (
        "Do not edit the prepared Source Parent. Confirm that its automatically generated "
        "inline equation remains inside one ordinary text line without a blank line."
    )
    copy_ui_acceptance_instruction = (
        "the inline equation stays within the surrounding sentence and the prepared rich "
        "text, table, and image remain visually equivalent, with no added blank line"
    )

    async def build_scaffold(self, context: FixtureContext) -> FixtureBuildResult:
        recorder = context.recorder
        section = recorder.record_structure(
            "canvas_section",
            await ensure_section(context.client, context.notebook_id, "Source"),
        )
        page = recorder.record_structure(
            "canvas_page",
            await ensure_page(
                context.client,
                str(section["id"]),
                self.canvas_title,
                "Synthetic manual-validation Source Parent with an automatic inline equation.",
            ),
        )
        page, copy_fixture = await ensure_copy_rich_fixture(
            context.client,
            page,
            context.options.run_dir,
            include_equations=False,
        )
        appended = await context.client.call_tool(
            "append_page_content",
            {
                "page_id": str(page["id"]),
                "content": (
                    "<p><span>INLINE_EQUATION_FIXTURE_V1 before </span>"
                    f'<math xmlns="{MATHML_NAMESPACE}"><mrow><mi>E</mi><mo>=</mo>'
                    "<mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math>"
                    "<span> after.</span></p>"
                ),
                "content_format": "html",
                "expected_title": display_name(page),
                "expected_section_id": str(section["id"]),
                "expected_modified": page.get("modified"),
                "x": 36.0,
                "y": 360.0,
            },
        )
        page = dict(appended["item"])
        final_xml = str(
            (
                await context.client.call_tool(
                    "get_page_xml",
                    {"page_id": str(page["id"]), "page_info": "all"},
                )
            )["xml"]
        )
        inline_projection = mathml_structure_projection(final_xml)
        automated_content = list(copy_fixture.get("automated_content", ()))
        automated_content.append("inline_equation")
        copy_fixture["automated_content"] = automated_content
        copy_fixture["inline_equation_structure"] = inline_projection
        recorder.refresh_structure("canvas_page", page)
        recorder.record_evidence("copy_fixture", copy_fixture)
        return FixtureBuildResult(recorder.structure, recorder.evidence)

    def content_report(
        self,
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        report = super().content_report(snapshot, page_id)
        math = report.get("mathml_structure_projection")
        candidates = math.get("candidates", ()) if isinstance(math, Mapping) else ()
        candidate = candidates[0] if len(candidates) == 1 else {}
        equation_count = (
            int(math.get("semantic_mathml", {}).get("equation_count", 0))
            if isinstance(math, Mapping)
            else 0
        )
        inline_valid = (
            isinstance(math, Mapping)
            and math.get("complete") is True
            and equation_count == 1
            and math.get("candidate_text_node_count") == 1
            and math.get("display_attribute_equation_count") == 0
            and math.get("standalone_candidate_count") == 0
            and isinstance(candidate, Mapping)
            and candidate.get("inline_visible_text_context") is True
            and candidate.get("oe_direct_t_break_count") == 0
        )
        unexpected = set(report.get("unexpected", ()))
        unexpected.discard("formula-t-has-visible-residual-text")
        if report.get("display_attribute_observed") is True:
            unexpected.add("unexpected-display-attribute")
        inline_break_count = (
            int(candidate.get("oe_direct_t_break_count", 0))
            if isinstance(candidate, Mapping)
            else 0
        )
        if inline_break_count:
            unexpected.add(f"inline-break-count:{inline_break_count}")
        missing = [] if inline_valid else [self.capability]
        passed = (
            inline_valid
            and not unexpected
            and report.get("invalid_object_schema_count") == 0
            and isinstance(report.get("capability_projection"), Mapping)
            and report["capability_projection"].get("complete") is True
        )
        report.update(
            {
                "schema_version": 2,
                "observed": ({self.capability: 1} if inline_valid else {}),
                "missing": missing,
                "unexpected": sorted(unexpected),
                "display_attribute_observed": False,
                "representation_status": "inline_mathml_observed" if passed else "mismatch",
                "template_publish_allowed": passed,
                "passed": passed,
            }
        )
        return report

    def compare_copy_readback(
        self,
        source_xml: str,
        target_xml: str,
        copy_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        comparison = super().compare_copy_readback(
            source_xml,
            target_xml,
            copy_report,
        )
        source_structure = comparison["source_mathml_structure"]
        target_structure = comparison["target_mathml_structure"]

        def one_inline(projection: Mapping[str, Any]) -> bool:
            candidates = projection.get("candidates", ())
            candidate = candidates[0] if len(candidates) == 1 else {}
            return (
                projection.get("complete") is True
                and projection.get("semantic_mathml", {}).get("equation_count") == 1
                and projection.get("candidate_text_node_count") == 1
                and projection.get("display_attribute_equation_count") == 0
                and projection.get("standalone_candidate_count") == 0
                and isinstance(candidate, Mapping)
                and candidate.get("inline_visible_text_context") is True
                and candidate.get("oe_direct_t_break_count") == 0
            )

        required = dict(comparison["checks"])
        required.pop("one_standalone_equation_each", None)
        required["one_inline_equation_each"] = one_inline(
            source_structure
        ) and one_inline(target_structure)
        required["no_break_around_inline_equation"] = all(
            projection.get("candidates", ())[0].get("oe_direct_t_break_count") == 0
            for projection in (source_structure, target_structure)
            if len(projection.get("candidates", ())) == 1
        ) and all(
            len(projection.get("candidates", ())) == 1
            for projection in (source_structure, target_structure)
        )
        comparison.update(
            {
                "checks": required,
                "exact_structure_equal": source_structure == target_structure,
                "passed": all(required.values()),
            }
        )
        return comparison


class InkDrawingInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "InkDrawing"
    requested_object_types = frozenset({"InkDrawing"})
    authoring_instruction = "Draw exactly one small synthetic ink stroke on this Canvas."
    geometry_absolute_tolerance = Decimal("0.0001")
    geometry_fields = {
        "Position": frozenset({"x", "y", "z"}),
        "Size": frozenset({"width", "height"}),
    }

    @staticmethod
    def _ink_projection(xml: str) -> list[dict[str, Any]]:
        """Hash stable Ink subtrees while retaining layout/data in the comparison."""

        ignored = GENERATED_OBJECT_ATTRIBUTES | VOLATILE_ATTRIBUTES

        def project_node(node) -> dict[str, Any]:
            attributes = dict(
                sorted(
                    (local_name(key), value)
                    for key, value in node.attrib.items()
                    if local_name(key) not in ignored
                )
            )
            text = "".join((node.text or "").split())
            return {
                "kind": local_name(node.tag),
                "attributes": attributes,
                "text_chars": len(text),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "children": [project_node(child) for child in list(node)],
            }

        return [
            project_node(node)
            for node in parse_xml(xml).iter()
            if local_name(node.tag) == "InkDrawing"
        ]

    @classmethod
    def _compare_ink_projections(
        cls,
        source: list[dict[str, Any]],
        target: list[dict[str, Any]],
    ) -> dict[str, Any]:
        geometry_deltas: list[dict[str, Any]] = []
        mismatch_paths: list[str] = []
        structure_and_data_equal = len(source) == len(target)
        geometry_within_tolerance = True

        def compare_node(
            left: Mapping[str, Any],
            right: Mapping[str, Any],
            path: str,
        ) -> None:
            nonlocal structure_and_data_equal, geometry_within_tolerance
            left_kind = str(left.get("kind", ""))
            right_kind = str(right.get("kind", ""))
            if left_kind != right_kind:
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#kind")
                return

            left_attributes = left.get("attributes", {})
            right_attributes = right.get("attributes", {})
            if not isinstance(left_attributes, Mapping) or not isinstance(
                right_attributes, Mapping
            ):
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#attribute-schema")
                return
            if set(left_attributes) != set(right_attributes):
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#attribute-names")

            geometry_names = cls.geometry_fields.get(left_kind, frozenset())
            for name in sorted(set(left_attributes) & set(right_attributes)):
                left_value = str(left_attributes[name])
                right_value = str(right_attributes[name])
                if name not in geometry_names:
                    if left_value != right_value:
                        structure_and_data_equal = False
                        mismatch_paths.append(f"{path}@{name}")
                    continue
                try:
                    left_number = Decimal(left_value)
                    right_number = Decimal(right_value)
                    if not left_number.is_finite() or not right_number.is_finite():
                        raise InvalidOperation
                    delta = abs(left_number - right_number)
                except InvalidOperation:
                    geometry_within_tolerance = False
                    mismatch_paths.append(f"{path}@{name}#non-numeric")
                    geometry_deltas.append(
                        {
                            "path": path,
                            "field": name,
                            "source": left_value,
                            "target": right_value,
                            "absolute_delta": None,
                            "within_tolerance": False,
                        }
                    )
                    continue
                within_tolerance = delta <= cls.geometry_absolute_tolerance
                geometry_within_tolerance = (
                    geometry_within_tolerance and within_tolerance
                )
                if not within_tolerance:
                    mismatch_paths.append(f"{path}@{name}#outside-tolerance")
                geometry_deltas.append(
                    {
                        "path": path,
                        "field": name,
                        "source": left_value,
                        "target": right_value,
                        "absolute_delta": str(delta),
                        "within_tolerance": within_tolerance,
                    }
                )

            for field in ("text_chars", "text_sha256"):
                if left.get(field) != right.get(field):
                    structure_and_data_equal = False
                    mismatch_paths.append(f"{path}#{field}")

            left_children = left.get("children", ())
            right_children = right.get("children", ())
            if not isinstance(left_children, list) or not isinstance(
                right_children, list
            ):
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#children-schema")
                return
            if len(left_children) != len(right_children):
                structure_and_data_equal = False
                mismatch_paths.append(f"{path}#children-count")
            for index, (left_child, right_child) in enumerate(
                zip(left_children, right_children, strict=False)
            ):
                compare_node(left_child, right_child, f"{path}/child[{index}]")

        for index, (left, right) in enumerate(zip(source, target, strict=False)):
            compare_node(left, right, f"/InkDrawing[{index}]")

        numeric_deltas = [
            Decimal(str(value["absolute_delta"]))
            for value in geometry_deltas
            if value["absolute_delta"] is not None
        ]
        return {
            "geometry_absolute_tolerance": str(cls.geometry_absolute_tolerance),
            "geometry_deltas": geometry_deltas,
            "max_geometry_absolute_delta": (
                str(max(numeric_deltas)) if numeric_deltas else None
            ),
            "structure_and_data_equal": structure_and_data_equal,
            "geometry_within_tolerance": geometry_within_tolerance,
            "mismatch_paths": sorted(set(mismatch_paths)),
            "passed": structure_and_data_equal and geometry_within_tolerance,
        }

    def compare_copy_readback(
        self,
        source_xml: str,
        target_xml: str,
        copy_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Compare Ink geometry/data without requiring unrelated Page XML identity."""

        source_projection = self._ink_projection(source_xml)
        target_projection = self._ink_projection(target_xml)
        source_payload = json.dumps(
            source_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        target_payload = json.dumps(
            target_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        page_results = copy_report.get("page_results", ())
        page_result = page_results[0] if len(page_results) == 1 else {}
        equivalence = page_result.get("equivalence", {}) if isinstance(page_result, dict) else {}
        checks = equivalence.get("checks", {}) if isinstance(equivalence, dict) else {}
        projection_comparison = self._compare_ink_projections(
            source_projection,
            target_projection,
        )
        required = {
            "one_source_ink_drawing": len(source_projection) == 1,
            "one_target_ink_drawing": len(target_projection) == 1,
            "ink_structure_and_data_projection": projection_comparison[
                "structure_and_data_equal"
            ],
            "ink_geometry_within_tolerance": projection_comparison[
                "geometry_within_tolerance"
            ],
            "content_objects": checks.get("content_objects") is True,
            "binary_sha256": checks.get("binary_sha256") is True,
            "visible_text": checks.get("visible_text") is True,
        }
        return {
            "verification_tier": "semantic_ink_drawing",
            "checks": required,
            "canonical_xml_observed": checks.get("canonical_xml") is True,
            "canonical_xml_required": False,
            "source_projection_sha256": hashlib.sha256(
                source_payload.encode("utf-8")
            ).hexdigest(),
            "target_projection_sha256": hashlib.sha256(
                target_payload.encode("utf-8")
            ).hexdigest(),
            "source_ink_count": len(source_projection),
            "target_ink_count": len(target_projection),
            "exact_ink_projection_equal": source_projection == target_projection,
            "ink_projection_comparison": projection_comparison,
            "passed": all(required.values()),
        }


class MediaFileInteractiveFixtureRecipe(InteractiveFixtureRecipe):
    capability = "MediaFile"
    requested_object_types = frozenset({"MediaFile"})
    recipe_version = 8
    authoring_instruction = (
        "Use OneNote Insert > Record Video to create exactly one 1-2 second synthetic "
        "video recording on this Canvas; do not attach or drag an existing media file."
    )


class UIShapeInteractiveFixtureRecipe(InkDrawingInteractiveFixtureRecipe):
    """Classify a UI Shape as InkDrawing plus the observed ShapeInfo marker."""

    capability = "UIShape"
    requested_object_types = frozenset({"InkDrawing"})
    requested_projected_capabilities = frozenset({"UIShape"})
    recipe_version = 5
    geometry_absolute_tolerance = Decimal("0.02")
    successful_representation_status = "requested_composite_observed"
    authoring_instruction = (
        "Use OneNote Draw > Shapes to add exactly one small synthetic rectangle; "
        "do not draw freehand ink or add any other content object."
    )

    def content_report(
        self,
        snapshot: Mapping[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        report = super().content_report(snapshot, page_id)
        projection = report.get("capability_projection")
        marker_counts = (
            projection.get("structural_marker_counts")
            if isinstance(projection, Mapping)
            else None
        )
        marker_schema_valid = (
            isinstance(marker_counts, Mapping)
            and all(
                isinstance(name, str)
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
                for name, count in marker_counts.items()
            )
        )
        shape_info_count = (
            int(marker_counts.get("ShapeInfo", 0)) if marker_schema_valid else 0
        )
        anchor_point_count = (
            int(marker_counts.get("AnchorPoint", 0)) if marker_schema_valid else 0
        )
        marker_contract_passed = marker_schema_valid and shape_info_count == 1
        unexpected = set(report.get("unexpected", ()))
        missing = set(report.get("missing", ()))
        if not marker_schema_valid:
            unexpected.add("invalid-structural-marker-schema")
        elif shape_info_count != 1:
            unexpected.add(f"structural-marker-count:ShapeInfo={shape_info_count}")
            missing.add(self.capability)
        report.update(
            {
                "schema_version": 4,
                "missing": sorted(missing),
                "unexpected": sorted(unexpected),
                "structural_marker_counts": (
                    dict(sorted(marker_counts.items())) if marker_schema_valid else {}
                ),
                "shape_info_count": shape_info_count,
                "anchor_point_count": anchor_point_count,
                "structural_marker_schema_valid": marker_schema_valid,
                "representation_status": (
                    self.successful_representation_status
                    if report.get("passed") is True and marker_contract_passed
                    else "mismatch"
                ),
                "template_publish_allowed": (
                    report.get("passed") is True and marker_contract_passed
                ),
                "passed": report.get("passed") is True and marker_contract_passed,
            }
        )
        return report

    @staticmethod
    def _shape_marker_counts(xml: str) -> Counter:
        return Counter(
            local_name(node.tag)
            for ink in parse_xml(xml).iter()
            if local_name(ink.tag) == "InkDrawing"
            for node in ink.iter()
            if local_name(node.tag) in {"ShapeInfo", "AnchorPoint"}
        )

    def compare_copy_readback(
        self,
        source_xml: str,
        target_xml: str,
        copy_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        comparison = super().compare_copy_readback(
            source_xml,
            target_xml,
            copy_report,
        )
        source_markers = self._shape_marker_counts(source_xml)
        target_markers = self._shape_marker_counts(target_xml)
        shape_checks = {
            "one_source_shape_info": source_markers["ShapeInfo"] == 1,
            "one_target_shape_info": target_markers["ShapeInfo"] == 1,
            "shape_marker_counts_equal": source_markers == target_markers,
        }
        comparison["verification_tier"] = "semantic_ui_shape"
        comparison["checks"] = {**comparison["checks"], **shape_checks}
        comparison["source_shape_marker_counts"] = dict(sorted(source_markers.items()))
        comparison["target_shape_marker_counts"] = dict(sorted(target_markers.items()))
        comparison["passed"] = comparison["passed"] and all(shape_checks.values())
        return comparison


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
            FixtureValidationContext(
                args=role.args,
                snapshot=role.snapshot,
                role="source",
            ),
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
    "InlineEquationInteractiveFixtureRecipe",
    "InsertedFileInteractiveFixtureRecipe",
    "InteractiveBootstrapRequired",
    "InteractiveFixtureRecipe",
    "MediaFileInteractiveFixtureRecipe",
    "UIShapeInteractiveFixtureRecipe",
    "UserAuthoredRecipe",
]
