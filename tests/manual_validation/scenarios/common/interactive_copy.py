"""Copy-only evidence runtime for one statically bound interactive capability."""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from local_onenote_mcp.page.copying import page_binary_hashes

from ...mcp_stdio_client import ClientFailure, MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import (
    capture_snapshot,
    display_name,
    find_snapshot_item,
    mathml_oe_adjacency_projection,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
    write_sensitive_page_xml,
)
from ...run_identity import run_safe_timestamp
from ..base import Scenario
from .copy_invariants import assert_copy_mapping, assert_pages_unchanged
from .copy_runtime import call_with_result_evidence, stable_copy_plan
from .interactive_bootstrap import MAX_INTERACTIVE_TIMEOUT, _bounded_input


def build_interactive_copy_comparison(
    recipe,
    *,
    source_report: Mapping[str, Any],
    target_report: Mapping[str, Any],
    source_objects: tuple[Mapping[str, Any], ...],
    target_objects: tuple[Mapping[str, Any], ...],
    copy_report: Mapping[str, Any],
    source_xml: str,
    target_xml: str,
    binary_evidence: Mapping[str, Any] | None = None,
    diagnostic_partial_admitted: bool = False,
) -> dict[str, Any]:
    """Combine public-kind, capability, production read-back, and issue gates."""

    object_comparison = recipe.compare_capability(source_objects, target_objects)
    page_results = copy_report.get("page_results", ())
    page_result = page_results[0] if len(page_results) == 1 else {}
    equivalence = page_result.get("equivalence", {}) if isinstance(page_result, dict) else {}
    checks = equivalence.get("checks", {}) if isinstance(equivalence, dict) else {}
    strict_equivalence = {
        "canonical_xml": checks.get("canonical_xml") is True,
        "content_objects": checks.get("content_objects") is True,
        "binary_sha256": checks.get("binary_sha256") is True,
        "visible_text": checks.get("visible_text") is True,
    }
    issues = [value for value in copy_report.get("issues", ()) if isinstance(value, dict)]
    production_copy_contract = copy_report.get("copy_contract_satisfied") is True
    issue_contract = (
        not issues
        if production_copy_contract
        else bool(issues)
        and all(
            issue.get("code") == recipe.copy_issue_code
            and issue.get("content_type") == recipe.capability
            and issue.get("action") == recipe.copy_issue_action
            for issue in issues
        )
    )
    no_omitted_content = not copy_report.get("skipped_content")
    production_verified = copy_report.get("verified") is True
    capability_readback = recipe.compare_copy_readback(
        source_xml,
        target_xml,
        copy_report,
    )
    temporary_known_com_normalization = (
        capability_readback.get("temporary_known_com_normalization_accepted") is True
    )
    if not issues and (
        copy_report.get("lossless") is True or temporary_known_com_normalization
    ):
        issue_contract = True
    production_result_admitted = production_verified or (
        diagnostic_partial_admitted and capability_readback.get("passed") is True
    )
    binary_evidence = dict(binary_evidence or {})
    direct_binary_equal = binary_evidence.get("equal", True) is True
    passed = (
        source_report.get("passed") is True
        and target_report.get("passed") is True
        and object_comparison.get("equivalent") is True
        and production_result_admitted
        and capability_readback.get("passed") is True
        and issue_contract
        and no_omitted_content
        and direct_binary_equal
    )
    return {
        "schema_version": 2,
        "capability": recipe.capability,
        "source_detection": dict(source_report),
        "target_detection": dict(target_report),
        "object_comparison": object_comparison,
        "production_copy": {
            "verified": production_verified,
            "lossless": copy_report.get("lossless"),
            "equivalence": equivalence,
            "issues": issues,
            "skipped_content": list(copy_report.get("skipped_content", ())),
            "binary_evidence": binary_evidence,
        },
        "capability_readback": capability_readback,
        "checks": {
            "source_detector_passed": source_report.get("passed") is True,
            "target_detector_passed": target_report.get("passed") is True,
            "public_object_signature_equivalent": object_comparison.get("equivalent") is True,
            "production_verified": production_verified,
            "production_equivalence": equivalence.get("equivalent") is True,
            "strict_equivalence_checks": all(strict_equivalence.values()),
            "capability_readback_passed": capability_readback.get("passed") is True,
            "diagnostic_partial_admitted": diagnostic_partial_admitted,
            "temporary_known_com_normalization_accepted": (
                temporary_known_com_normalization
            ),
            "production_result_admitted_for_evidence": production_result_admitted,
            "issues_match_copy_fidelity_contract": issue_contract,
            "no_omitted_content": no_omitted_content,
            "direct_binary_sha256_equal": direct_binary_equal,
        },
        "passed": passed,
    }


def assess_diagnostic_partial_copy(
    result: Mapping[str, Any],
    *,
    source_id: str,
    capability: str,
    temporary_known_com_normalization: bool = False,
) -> dict[str, Any]:
    """Admit only a completed Copy whose sole failure is read-back verification."""

    copy_report = result.get("copy_report", {})
    id_map = copy_report.get("id_map", {}) if isinstance(copy_report, Mapping) else {}
    target_id = str(id_map.get(source_id, "")) if isinstance(id_map, Mapping) else ""
    created_ids = [str(value) for value in result.get("created_ids", ())]
    resolved_ids = [str(value) for value in result.get("resolved_target_ids", ())]
    page_results = copy_report.get("page_results", ()) if isinstance(copy_report, Mapping) else ()
    page_result = page_results[0] if len(page_results) == 1 else {}
    equivalence = page_result.get("equivalence", {}) if isinstance(page_result, Mapping) else {}
    checks = equivalence.get("checks", {}) if isinstance(equivalence, Mapping) else {}
    issues = copy_report.get("issues", ()) if isinstance(copy_report, Mapping) else ()
    expected_issues = bool(issues) and all(
        isinstance(issue, Mapping)
        and issue.get("code") == "content_type_unverified"
        and issue.get("content_type") == capability
        and issue.get("action") == "preserved_unverified"
        for issue in issues
    )
    completed_operations = [
        value.get("operation")
        for value in result.get("completed_steps", ())
        if isinstance(value, Mapping)
    ]
    gates = {
        "structured_verify_only_partial": (
            result.get("code") == "partial_failure"
            and result.get("partial") is True
            and result.get("complete") is False
            and result.get("failed_step") == "verify_copy"
            and result.get("outcome") == "copy_unverified"
        ),
        "source_untouched": (
            result.get("source_deleted") is False
            and result.get("source_touched") is False
            and result.get("source_untouched") is True
        ),
        "one_exact_created_target": (
            bool(target_id)
            and created_ids == [target_id]
            and resolved_ids == [target_id]
        ),
        "one_exact_page_mapping": (
            isinstance(id_map, Mapping)
            and dict(id_map) == {source_id: target_id}
            and page_result.get("source_page_id") == source_id
            and page_result.get("target_page_id") == target_id
        ),
        "copy_write_and_reorder_completed": completed_operations
        == ["create", "write_page_content", "reorder_pages"],
        "only_expected_unverified_issue_or_known_com_normalization": (
            expected_issues
            or (temporary_known_com_normalization and not issues)
        ),
        "no_omitted_content": not copy_report.get("skipped_content"),
        "noncanonical_checks_passed": (
            checks.get("visible_text") is True
            and checks.get("content_objects") is True
            and checks.get("binary_sha256") is True
        ),
        "canonical_is_the_observed_failure": (
            checks.get("canonical_xml") is False
            and equivalence.get("equivalent") is False
            and copy_report.get("verified") is False
        ),
        "temporary_normalization_scope_valid": (
            not temporary_known_com_normalization
            or capability == "DisplayEquation"
        ),
    }
    return {
        "schema_version": 1,
        "capability": capability,
        "temporary_known_com_normalization": temporary_known_com_normalization,
        "source_page_id": source_id,
        "target_page_id": target_id,
        "gates": gates,
        "admitted": all(gates.values()),
    }


def _copy_target_id(result: Mapping[str, Any], source_id: str) -> str:
    target = result.get("item") or result.get("destination") or {}
    if isinstance(target, Mapping) and target.get("id"):
        return str(target["id"])
    copy_report = result.get("copy_report", {})
    id_map = copy_report.get("id_map", {}) if isinstance(copy_report, Mapping) else {}
    if isinstance(id_map, Mapping) and id_map.get(source_id):
        return str(id_map[source_id])
    partial_id_map = result.get("id_map", {})
    if isinstance(partial_id_map, Mapping) and partial_id_map.get(source_id):
        return str(partial_id_map[source_id])
    resolved_target_ids = result.get("resolved_target_ids", [])
    if (
        isinstance(resolved_target_ids, list)
        and len(resolved_target_ids) == 1
        and resolved_target_ids[0]
    ):
        return str(resolved_target_ids[0])
    return ""


async def capture_copy_xml_structure_evidence(
    client: MCPStdioClient,
    *,
    source_id: str,
    target_id: str,
    evidence_path,
    capture_page_xml: bool = False,
    source_xml_path=None,
    target_xml_path=None,
    sensitive_manifest_path=None,
    case_name: str = "same-section",
) -> tuple[str, str]:
    """Read both Pages and persist content-free MathML/OE adjacency evidence."""

    source_xml = str(
        (
            await client.call_tool(
                "get_page_xml",
                {"page_id": source_id, "page_info": "all"},
            )
        )["xml"]
    )
    target_xml = str(
        (
            await client.call_tool(
                "get_page_xml",
                {"page_id": target_id, "page_info": "all"},
            )
        )["xml"]
    )
    source_projection = mathml_oe_adjacency_projection(source_xml)
    target_projection = mathml_oe_adjacency_projection(target_xml)
    write_json(
        evidence_path,
        {
            "schema_version": 1,
            "source_page_id": source_id,
            "target_page_id": target_id,
            "source": source_projection,
            "target": target_projection,
            "exact_projection_equal": source_projection == target_projection,
            "content_exposed": False,
            "passed": True,
        },
    )
    if capture_page_xml:
        if source_xml_path is None or target_xml_path is None or sensitive_manifest_path is None:
            raise InvariantFailure("Sensitive Page XML capture paths were not fully bound.")
        source_capture = write_sensitive_page_xml(source_xml_path, source_xml)
        target_capture = write_sensitive_page_xml(target_xml_path, target_xml)
        write_json(
            sensitive_manifest_path,
            {
                "schema_version": 1,
                "case": case_name,
                "opt_in_argument": "--capture-page-xml",
                "contains_user_authored_body_text": True,
                "raw_oe_t_mathml_retained": True,
                "binary_data_redacted": True,
                "source": source_capture,
                "target": target_capture,
                "content_classification": "sensitive_local_validation_evidence",
            },
        )
    return source_xml, target_xml


class InteractiveCopyEvidenceScenario(Scenario):
    """Copy one cached authored Page without Delete or runtime allowlist changes."""

    included_in_all = False
    timeout_default = 1_800
    worksite_dry_run_action = "preserve-interactive-copy-evidence-target"
    include_cross_section_case = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--interactive-timeout",
            type=int,
            default=900,
            help="Bounded seconds for the exact run-bound Copy UI verdict (max 1800).",
        )
        parser.add_argument(
            "--capture-page-xml",
            action="store_true",
            help=(
                "Opt in to sensitive source/target Page XML evidence; body text and "
                "OE/T/MathML are retained while embedded Data payloads are redacted."
            ),
        )
        parser.add_argument(
            "--copy-chain-length",
            type=int,
            default=1,
            help=(
                "Copy each target again as the next source for 1-5 bounded hops; "
                "every hop is retained and independently verified."
            ),
        )

    @staticmethod
    def _case_evidence_path(out, stem: str, case_name: str, hop_index: int = 1):
        suffix = "" if case_name == "same-section" else "-cross-section"
        if hop_index > 1:
            suffix += f"-chain-{hop_index}"
        return out / f"{stem}{suffix}.json"

    @staticmethod
    def _case_file_path(
        out, stem: str, case_name: str, extension: str, hop_index: int = 1
    ):
        suffix = "" if case_name == "same-section" else "-cross-section"
        if hop_index > 1:
            suffix += f"-chain-{hop_index}"
        return out / f"{stem}{suffix}.{extension}"

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        del fixture_result
        if client is None:
            raise InvariantFailure("Interactive Copy evidence requires its one scenario MCP client.")
        if args.interactive_timeout < 1 or args.interactive_timeout > MAX_INTERACTIVE_TIMEOUT:
            raise InvariantFailure("--interactive-timeout must be between 1 and 1800 seconds.")
        chain_length = int(getattr(args, "copy_chain_length", 1))
        if chain_length < 1 or chain_length > 5:
            raise InvariantFailure("--copy-chain-length must be between 1 and 5.")
        if chain_length > 1 and self.include_cross_section_case:
            raise InvariantFailure(
                "Chained Interactive Copy is limited to single-case scenarios."
            )
        validate_manifest_notebook(manifest, args.notebook_name)
        recipe = self.fixture_recipe
        cache = manifest.get("fixture_cache", {})
        if cache.get("template_instance_id") != recipe.default_template_instance_id:
            raise InvariantFailure("Live manifest instance differs from the interactive Recipe.")
        live_validation = cache.get("interactive_live_validation", {})
        if live_validation.get("passed") is not True:
            raise InvariantFailure("Cached interactive fixture did not pass live validation.")

        notebook_id = str(manifest["notebook"]["id"])
        source_id = str(manifest["structure"]["canvas_page"]["id"])
        source_section_id = str(manifest["structure"]["canvas_section"]["id"])
        out = scenario_dir(options.run_dir, self.name)
        current_snapshot = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", current_snapshot)
        source = find_snapshot_item(current_snapshot, source_id)
        if source is None or source.get("resource_type") != "page":
            raise InvariantFailure("Interactive Copy source Page is not active in its working copy.")
        if str(source.get("section_id", "")) != source_section_id:
            raise InvariantFailure("Interactive Copy source Page left its manifest Section.")
        source_report = recipe.content_report(current_snapshot, source_id)
        if source_report.get("passed") is not True:
            write_json(out / "source-detection.json", source_report)
            raise InvariantFailure("Interactive Copy source detector failed before mutation.")

        case_results: list[dict[str, Any]] = []
        run_stamp = run_safe_timestamp(args)
        case_count = 2 if self.include_cross_section_case else 1

        for case_index in range(case_count):
            case_name = "same-section" if case_index == 0 else "cross-section"
            destination_section_id = source_section_id
            destination_section_name = display_name(
                find_snapshot_item(current_snapshot, source_section_id) or {}
            )
            if case_name == "cross-section":
                destination_section_name = (
                    f"01-{recipe.capability}-Cross-Section-{run_stamp}"
                )
                if any(
                    item.get("resource_type") == "section"
                    and display_name(item) == destination_section_name
                    for item in current_snapshot.get("items", ())
                    if isinstance(item, Mapping)
                ):
                    raise InvariantFailure(
                        "Cross-Section Copy destination already existed before this run."
                    )
                created_section = await call_with_result_evidence(
                    client,
                    "create_section",
                    {
                        "parent_id": notebook_id,
                        "section_name": destination_section_name,
                    },
                    out / "destination-section-result.json",
                )
                destination_section = created_section.get("section", {})
                destination_section_id = str(destination_section.get("id", ""))
                if (
                    not destination_section_id
                    or destination_section.get("resource_type") != "section"
                    or destination_section.get("parent_id") != notebook_id
                    or display_name(destination_section) != destination_section_name
                    or destination_section_id == source_section_id
                ):
                    raise InvariantFailure(
                        "Cross-Section Copy did not create its exact destination Section."
                    )
                current_snapshot = await capture_snapshot(client, notebook_id)
                destination = find_snapshot_item(
                    current_snapshot, destination_section_id
                )
                if (
                    destination is None
                    or destination.get("resource_type") != "section"
                    or destination.get("parent_id") != notebook_id
                ):
                    raise InvariantFailure(
                        "Cross-Section Copy destination is not active before planning."
                    )
                write_json(out / "before-cross-section.json", current_snapshot)
                write_json(
                    out / "destination-section.json",
                    {
                        "schema_version": 1,
                        "source_section_id": source_section_id,
                        "destination_section_id": destination_section_id,
                        "destination_section_name": destination_section_name,
                        "distinct": True,
                        "active_before_plan": True,
                    },
                )

            destination_title = (
                f"02-{recipe.capability}-Copy-{run_stamp}"
                if case_name == "same-section"
                else f"03-{recipe.capability}-Cross-Section-Copy-{run_stamp}"
            )
            plan_arguments = {
                "source_id": source_id,
                "destination_parent_id": destination_section_id,
                "destination_name": destination_title,
                "include_descendants": False,
            }
            planned = await stable_copy_plan(
                client,
                plan_arguments,
                attempts_path=self._case_evidence_path(
                    out, "plan-attempts", case_name
                ),
                plan_path=self._case_evidence_path(out, "plan", case_name),
            )
            if planned.get("include_descendants") is not False:
                raise InvariantFailure(
                    "Interactive Copy plan unexpectedly included descendants."
                )
            required_plan_capabilities = set(
                recipe.required_plan_capabilities or {recipe.capability}
            )
            observed_plan_capabilities = set(
                planned.get("content_capabilities", ())
            )
            if not required_plan_capabilities <= observed_plan_capabilities:
                raise InvariantFailure(
                    "Interactive Copy plan did not observe required capabilities: "
                    f"{sorted(required_plan_capabilities - observed_plan_capabilities)}."
                )

            case_before = current_snapshot
            current_source = find_snapshot_item(case_before, source_id)
            if current_source is None:
                raise InvariantFailure(
                    "Interactive Copy source disappeared before a Copy case."
                )
            case_source_report = recipe.content_report(case_before, source_id)
            if case_source_report.get("passed") is not True:
                raise InvariantFailure(
                    f"Interactive Copy source detector failed before {case_name}."
                )
            diagnostic_partial_admitted = False
            copy_xml_pair: tuple[str, str] | None = None
            try:
                copied = await call_with_result_evidence(
                    client,
                    "copy_page",
                    {
                        "page_id": source_id,
                        "destination_section_id": destination_section_id,
                        "expected_title": display_name(current_source),
                        "expected_section_id": current_source.get("section_id"),
                        "expected_modified": planned.get("source", {}).get("modified"),
                        "destination_title": destination_title,
                        "include_descendants": False,
                        "plan_digest": planned["plan_digest"],
                    },
                    self._case_evidence_path(out, "copy-result", case_name),
                )
            except ClientFailure as exc:
                if not isinstance(exc.envelope, dict):
                    raise
                copied = dict(exc.envelope)
                partial_target_id = _copy_target_id(copied, source_id)
                if partial_target_id:
                    structure_path = self._case_evidence_path(
                        out, "page-xml-structure", case_name
                    )
                    try:
                        copy_xml_pair = await capture_copy_xml_structure_evidence(
                            client,
                            source_id=source_id,
                            target_id=partial_target_id,
                            evidence_path=structure_path,
                            capture_page_xml=bool(
                                getattr(args, "capture_page_xml", False)
                            ),
                            source_xml_path=self._case_file_path(
                                out, "source-page", case_name, "xml"
                            ),
                            target_xml_path=self._case_file_path(
                                out, "target-page", case_name, "xml"
                            ),
                            sensitive_manifest_path=self._case_evidence_path(
                                out, "sensitive-evidence", case_name
                            ),
                            case_name=case_name,
                        )
                    except Exception as evidence_error:
                        write_json(
                            structure_path,
                            {
                                "schema_version": 1,
                                "source_page_id": source_id,
                                "target_page_id": partial_target_id,
                                "content_exposed": False,
                                "passed": False,
                                "read_error_type": type(evidence_error).__name__,
                            },
                        )
                partial_assessment = assess_diagnostic_partial_copy(
                    copied,
                    source_id=source_id,
                    capability=recipe.capability,
                    temporary_known_com_normalization=(
                        bool(copy_xml_pair)
                        and recipe.compare_copy_readback(
                            copy_xml_pair[0],
                            copy_xml_pair[1],
                            copied.get("copy_report", {}),
                        ).get("temporary_known_com_normalization_accepted")
                        is True
                    ),
                )
                write_json(
                    self._case_evidence_path(
                        out, "partial-result-admission", case_name
                    ),
                    partial_assessment,
                )
                if partial_assessment.get("admitted") is not True:
                    raise
                diagnostic_partial_admitted = True

            copy_report = copied.get("copy_report", {})
            target = copied.get("item") or copied.get("destination") or {}
            target_id = str(target.get("id", ""))
            if not target_id:
                raise InvariantFailure(
                    "Interactive Copy did not return its exact target Page ID."
                )
            if copy_xml_pair is None:
                copy_xml_pair = await capture_copy_xml_structure_evidence(
                    client,
                    source_id=source_id,
                    target_id=target_id,
                    evidence_path=self._case_evidence_path(
                        out, "page-xml-structure", case_name
                    ),
                    capture_page_xml=bool(getattr(args, "capture_page_xml", False)),
                    source_xml_path=self._case_file_path(
                        out, "source-page", case_name, "xml"
                    ),
                    target_xml_path=self._case_file_path(
                        out, "target-page", case_name, "xml"
                    ),
                    sensitive_manifest_path=self._case_evidence_path(
                        out, "sensitive-evidence", case_name
                    ),
                    case_name=case_name,
                )
            case_after = await capture_snapshot(client, notebook_id)
            write_json(
                (
                    out / "after.json"
                    if case_index == case_count - 1
                    else out / "after-same-section.json"
                ),
                case_after,
            )
            assert_copy_mapping(
                case_before,
                case_after,
                source_id,
                destination_section_id,
                destination_title,
                copied,
                include_descendants=False,
            )
            protected_page_ids = [
                str(item["id"])
                for item in case_before.get("items", ())
                if isinstance(item, Mapping)
                and item.get("resource_type") == "page"
                and item.get("id")
            ]
            assert_pages_unchanged(
                case_before, case_after, protected_page_ids
            )

            target_report = recipe.copy_target_content_report(case_after, target_id)
            source_objects = tuple(
                recipe.detect_authored_content(case_before, source_id)
            )
            target_objects = tuple(
                recipe.detect_authored_content(case_after, target_id)
            )
            source_xml, target_xml = copy_xml_pair
            source_binary_hashes = page_binary_hashes(source_xml)
            target_binary_hashes = page_binary_hashes(target_xml)
            binary_evidence = {
                "source_payload_count": len(source_binary_hashes),
                "target_payload_count": len(target_binary_hashes),
                "payload_hashes_recorded": bool(
                    source_binary_hashes or target_binary_hashes
                ),
                "payloads_exposed": False,
                "source_sha256": source_binary_hashes,
                "target_sha256": target_binary_hashes,
                "equal": source_binary_hashes == target_binary_hashes,
            }
            comparison = build_interactive_copy_comparison(
                recipe,
                source_report=case_source_report,
                target_report=target_report,
                source_objects=source_objects,
                target_objects=target_objects,
                copy_report=copy_report,
                source_xml=source_xml,
                target_xml=target_xml,
                binary_evidence=binary_evidence,
                diagnostic_partial_admitted=diagnostic_partial_admitted,
            )
            write_json(
                self._case_evidence_path(
                    out, "machine-comparison", case_name
                ),
                comparison,
            )
            case_results.append(
                {
                    "case": case_name,
                    "cross_section": case_name == "cross-section",
                    "source_section_id": source_section_id,
                    "destination_section_id": destination_section_id,
                    "destination_section_name": destination_section_name,
                    "target_id": target_id,
                    "target_title": destination_title,
                    "machine_comparator_passed": comparison.get("passed") is True,
                    "production_lossless": copy_report.get("lossless"),
                    "production_verified": copy_report.get("verified"),
                    "diagnostic_partial_admitted": diagnostic_partial_admitted,
                    "verification_tier": comparison["capability_readback"][
                        "verification_tier"
                    ],
                    "temporary_known_com_normalization_accepted": comparison[
                        "capability_readback"
                    ].get("temporary_known_com_normalization_accepted")
                    is True,
                    "display_break_observation": comparison[
                        "capability_readback"
                    ].get("display_break_observation"),
                    "hops": [
                        {
                            "hop": 1,
                            "source_id": source_id,
                            "target_id": target_id,
                            "target_title": destination_title,
                            "machine_comparator_passed": comparison.get("passed")
                            is True,
                            "production_lossless": copy_report.get("lossless"),
                            "production_verified": copy_report.get("verified"),
                            "diagnostic_partial_admitted": (
                                diagnostic_partial_admitted
                            ),
                            "temporary_known_com_normalization_accepted": (
                                comparison["capability_readback"].get(
                                    "temporary_known_com_normalization_accepted"
                                )
                                is True
                            ),
                            "display_break_observation": comparison[
                                "capability_readback"
                            ].get("display_break_observation"),
                        }
                    ],
                }
            )
            current_snapshot = case_after

            chain_source_id = target_id
            for hop_index in range(2, chain_length + 1):
                chain_source = find_snapshot_item(current_snapshot, chain_source_id)
                if chain_source is None or chain_source.get("resource_type") != "page":
                    raise InvariantFailure(
                        f"Interactive Copy chain source is missing before hop {hop_index}."
                    )
                chain_source_report = recipe.content_report(
                    current_snapshot, chain_source_id
                )
                if chain_source_report.get("passed") is not True:
                    write_json(
                        self._case_evidence_path(
                            out, "source-detection", case_name, hop_index
                        ),
                        chain_source_report,
                    )
                    raise InvariantFailure(
                        f"Interactive Copy chain source detector failed before hop {hop_index}."
                    )

                chain_title = (
                    f"{hop_index + 1:02d}-{recipe.capability}-Copy-Chain-"
                    f"{hop_index}-{run_stamp}"
                )
                chain_plan_arguments = {
                    "source_id": chain_source_id,
                    "destination_parent_id": destination_section_id,
                    "destination_name": chain_title,
                    "include_descendants": False,
                }
                chain_plan = await stable_copy_plan(
                    client,
                    chain_plan_arguments,
                    attempts_path=self._case_evidence_path(
                        out, "plan-attempts", case_name, hop_index
                    ),
                    plan_path=self._case_evidence_path(
                        out, "plan", case_name, hop_index
                    ),
                )
                if chain_plan.get("include_descendants") is not False:
                    raise InvariantFailure(
                        f"Interactive Copy chain hop {hop_index} included descendants."
                    )
                if not required_plan_capabilities <= set(
                    chain_plan.get("content_capabilities", ())
                ):
                    raise InvariantFailure(
                        f"Interactive Copy chain hop {hop_index} lost required capabilities."
                    )

                chain_before = current_snapshot
                chain_partial_admitted = False
                chain_xml_pair: tuple[str, str] | None = None
                try:
                    chain_copied = await call_with_result_evidence(
                        client,
                        "copy_page",
                        {
                            "page_id": chain_source_id,
                            "destination_section_id": destination_section_id,
                            "expected_title": display_name(chain_source),
                            "expected_section_id": chain_source.get("section_id"),
                            "expected_modified": chain_plan.get("source", {}).get(
                                "modified"
                            ),
                            "destination_title": chain_title,
                            "include_descendants": False,
                            "plan_digest": chain_plan["plan_digest"],
                        },
                        self._case_evidence_path(
                            out, "copy-result", case_name, hop_index
                        ),
                    )
                except ClientFailure as exc:
                    if not isinstance(exc.envelope, dict):
                        raise
                    chain_copied = dict(exc.envelope)
                    partial_target_id = _copy_target_id(
                        chain_copied, chain_source_id
                    )
                    if partial_target_id:
                        chain_xml_pair = await capture_copy_xml_structure_evidence(
                            client,
                            source_id=chain_source_id,
                            target_id=partial_target_id,
                            evidence_path=self._case_evidence_path(
                                out, "page-xml-structure", case_name, hop_index
                            ),
                            capture_page_xml=bool(
                                getattr(args, "capture_page_xml", False)
                            ),
                            source_xml_path=self._case_file_path(
                                out, "source-page", case_name, "xml", hop_index
                            ),
                            target_xml_path=self._case_file_path(
                                out, "target-page", case_name, "xml", hop_index
                            ),
                            sensitive_manifest_path=self._case_evidence_path(
                                out, "sensitive-evidence", case_name, hop_index
                            ),
                            case_name=f"{case_name}-chain-{hop_index}",
                        )
                    temporary_normalization = (
                        bool(chain_xml_pair)
                        and recipe.compare_copy_readback(
                            chain_xml_pair[0],
                            chain_xml_pair[1],
                            chain_copied.get("copy_report", {}),
                        ).get("temporary_known_com_normalization_accepted")
                        is True
                    )
                    chain_partial = assess_diagnostic_partial_copy(
                        chain_copied,
                        source_id=chain_source_id,
                        capability=recipe.capability,
                        temporary_known_com_normalization=temporary_normalization,
                    )
                    write_json(
                        self._case_evidence_path(
                            out, "partial-result-admission", case_name, hop_index
                        ),
                        chain_partial,
                    )
                    if chain_partial.get("admitted") is not True:
                        failed_readback = (
                            recipe.compare_copy_readback(
                                chain_xml_pair[0],
                                chain_xml_pair[1],
                                chain_copied.get("copy_report", {}),
                            )
                            if chain_xml_pair
                            else {}
                        )
                        write_json(
                            out / "copy-chain.json",
                            {
                                "schema_version": 1,
                                "scenario": self.name,
                                "capability": recipe.capability,
                                "requested_chain_length": chain_length,
                                "completed_hops": hop_index,
                                "status": "failed_closed",
                                "failed_hop": hop_index,
                                "hops": [
                                    *case_results[-1]["hops"],
                                    {
                                        "hop": hop_index,
                                        "source_id": chain_source_id,
                                        "target_id": partial_target_id,
                                        "machine_comparator_passed": False,
                                        "temporary_known_com_normalization_accepted": False,
                                        "display_break_observation": failed_readback.get(
                                            "display_break_observation"
                                        ),
                                    },
                                ],
                                "content_exposed": False,
                            },
                        )
                        raise
                    chain_partial_admitted = True

                chain_copy_report = chain_copied.get("copy_report", {})
                chain_target_id = _copy_target_id(chain_copied, chain_source_id)
                if not chain_target_id:
                    raise InvariantFailure(
                        f"Interactive Copy chain hop {hop_index} returned no target ID."
                    )
                if chain_xml_pair is None:
                    chain_xml_pair = await capture_copy_xml_structure_evidence(
                        client,
                        source_id=chain_source_id,
                        target_id=chain_target_id,
                        evidence_path=self._case_evidence_path(
                            out, "page-xml-structure", case_name, hop_index
                        ),
                        capture_page_xml=bool(
                            getattr(args, "capture_page_xml", False)
                        ),
                        source_xml_path=self._case_file_path(
                            out, "source-page", case_name, "xml", hop_index
                        ),
                        target_xml_path=self._case_file_path(
                            out, "target-page", case_name, "xml", hop_index
                        ),
                        sensitive_manifest_path=self._case_evidence_path(
                            out, "sensitive-evidence", case_name, hop_index
                        ),
                        case_name=f"{case_name}-chain-{hop_index}",
                    )

                chain_after = await capture_snapshot(client, notebook_id)
                write_json(
                    self._case_evidence_path(
                        out, "after", case_name, hop_index
                    ),
                    chain_after,
                )
                assert_copy_mapping(
                    chain_before,
                    chain_after,
                    chain_source_id,
                    destination_section_id,
                    chain_title,
                    chain_copied,
                    include_descendants=False,
                )
                protected_chain_pages = [
                    str(item["id"])
                    for item in chain_before.get("items", ())
                    if isinstance(item, Mapping)
                    and item.get("resource_type") == "page"
                    and item.get("id")
                ]
                assert_pages_unchanged(
                    chain_before, chain_after, protected_chain_pages
                )

                chain_target_report = recipe.copy_target_content_report(
                    chain_after, chain_target_id
                )
                chain_source_objects = tuple(
                    recipe.detect_authored_content(chain_before, chain_source_id)
                )
                chain_target_objects = tuple(
                    recipe.detect_authored_content(chain_after, chain_target_id)
                )
                chain_source_xml, chain_target_xml = chain_xml_pair
                chain_source_binary = page_binary_hashes(chain_source_xml)
                chain_target_binary = page_binary_hashes(chain_target_xml)
                chain_comparison = build_interactive_copy_comparison(
                    recipe,
                    source_report=chain_source_report,
                    target_report=chain_target_report,
                    source_objects=chain_source_objects,
                    target_objects=chain_target_objects,
                    copy_report=chain_copy_report,
                    source_xml=chain_source_xml,
                    target_xml=chain_target_xml,
                    binary_evidence={
                        "source_payload_count": len(chain_source_binary),
                        "target_payload_count": len(chain_target_binary),
                        "payload_hashes_recorded": bool(
                            chain_source_binary or chain_target_binary
                        ),
                        "payloads_exposed": False,
                        "source_sha256": chain_source_binary,
                        "target_sha256": chain_target_binary,
                        "equal": chain_source_binary == chain_target_binary,
                    },
                    diagnostic_partial_admitted=chain_partial_admitted,
                )
                write_json(
                    self._case_evidence_path(
                        out, "machine-comparison", case_name, hop_index
                    ),
                    chain_comparison,
                )
                hop_result = {
                    "hop": hop_index,
                    "source_id": chain_source_id,
                    "target_id": chain_target_id,
                    "target_title": chain_title,
                    "machine_comparator_passed": chain_comparison.get("passed")
                    is True,
                    "production_lossless": chain_copy_report.get("lossless"),
                    "production_verified": chain_copy_report.get("verified"),
                    "diagnostic_partial_admitted": chain_partial_admitted,
                    "temporary_known_com_normalization_accepted": (
                        chain_comparison["capability_readback"].get(
                            "temporary_known_com_normalization_accepted"
                        )
                        is True
                    ),
                    "display_break_observation": chain_comparison[
                        "capability_readback"
                    ].get("display_break_observation"),
                }
                case_results[-1]["hops"].append(hop_result)
                case_results[-1]["target_id"] = chain_target_id
                case_results[-1]["target_title"] = chain_title
                case_results[-1]["machine_comparator_passed"] = all(
                    hop["machine_comparator_passed"]
                    for hop in case_results[-1]["hops"]
                )
                case_results[-1]["production_lossless"] = all(
                    hop["production_lossless"] is True
                    for hop in case_results[-1]["hops"]
                )
                case_results[-1]["production_verified"] = all(
                    hop["production_verified"] is True
                    for hop in case_results[-1]["hops"]
                )
                case_results[-1]["diagnostic_partial_admitted"] = any(
                    hop["diagnostic_partial_admitted"]
                    for hop in case_results[-1]["hops"]
                )
                case_results[-1][
                    "temporary_known_com_normalization_accepted"
                ] = all(
                    hop["temporary_known_com_normalization_accepted"]
                    for hop in case_results[-1]["hops"]
                )
                chain_source_id = chain_target_id
                current_snapshot = chain_after

            if chain_length > 1:
                write_json(out / "after.json", current_snapshot)
                write_json(
                    out / "copy-chain.json",
                    {
                        "schema_version": 1,
                        "scenario": self.name,
                        "capability": recipe.capability,
                        "requested_chain_length": chain_length,
                        "completed_hops": len(case_results[-1]["hops"]),
                        "hops": case_results[-1]["hops"],
                        "blank_line_accumulation": [
                            hop.get("display_break_observation")
                            for hop in case_results[-1]["hops"]
                        ],
                        "content_exposed": False,
                    },
                )

        run_id = options.run_dir.name
        accept = f"ACCEPT {run_id} {recipe.capability} COPY"
        reject = f"REJECT {run_id} {recipe.capability} COPY"
        target_summary = "; ".join(
            (
                (
                    f"{case['case']} target {case['target_title']!r}"
                    if chain_length == 1
                    else (
                        f"{case['case']} chain "
                        + " -> ".join(
                            hop["target_title"] for hop in case["hops"]
                        )
                    )
                )
                + f" in Section {case['destination_section_name']!r}"
            )
            for case in case_results
        )
        response = (
            await _bounded_input(
                (
                    f"Inspect {target_summary}; type {accept!r} if "
                    f"{recipe.copy_ui_acceptance_instruction}, or {reject!r} if not: "
                ),
                args.interactive_timeout,
            )
        ).strip()
        if response not in {accept, reject}:
            raise InvariantFailure("Interactive Copy verdict did not match this run.")
        human = {
            "schema_version": 2,
            "scenario": self.name,
            "capability": recipe.capability,
            "source_page_id": source_id,
            "target_page_id": case_results[0]["target_id"],
            "target_ids": [case["target_id"] for case in case_results],
            "cases": [
                {
                    key: case[key]
                    for key in (
                        "case",
                        "cross_section",
                        "source_section_id",
                        "destination_section_id",
                        "destination_section_name",
                        "target_id",
                        "target_title",
                        "hops",
                    )
                }
                for case in case_results
            ],
            "verdict": "accepted" if response == accept else "rejected",
            "confirmation_bound_to_run": True,
            "synthetic_content_only": True,
        }
        write_json(out / "human-acceptance.json", human)
        if response == reject:
            raise InvariantFailure("User rejected the interactive Copy UI result.")
        failed_cases = [
            case["case"]
            for case in case_results
            if case["machine_comparator_passed"] is not True
        ]
        if failed_cases:
            raise InvariantFailure(
                "Interactive Copy UI was accepted, but machine comparators failed "
                f"closed for cases: {failed_cases}."
            )

        target_ids = [
            hop["target_id"]
            for case in case_results
            for hop in case["hops"]
        ]
        remaining = {
            "status": "verified_copy_targets_retained",
            "target_ids": target_ids,
            "copy_chain_length": chain_length,
            "manual_cleanup_required": bool(getattr(args, "keep_worksite", False)),
            "reason": (
                "Copy evidence uses no Delete permission; disposable targets remain in "
                "the working artifact."
            ),
        }
        write_json(out / "worksite.json", remaining)
        result = {
            "scenario": self.name,
            "status": "passed",
            "capability": recipe.capability,
            "target_id": target_ids[0],
            "target_ids": target_ids,
            "copy_chain_length": chain_length,
            "cases": case_results,
            "same_section_validated": True,
            "cross_section_validated": self.include_cross_section_case,
            "machine_comparator_passed": True,
            "human_verdict": "accepted",
            "production_lossless": all(
                case["production_lossless"] is True for case in case_results
            ),
            "production_verified": all(
                case["production_verified"] is True for case in case_results
            ),
            "diagnostic_partial_admitted": any(
                case["diagnostic_partial_admitted"] for case in case_results
            ),
            "verification_tier": case_results[0]["verification_tier"],
            "source_deleted": False,
            "worksite_preserved": bool(getattr(args, "keep_worksite", False)),
            "remaining_state": remaining,
        }
        write_json(out / "result.json", result)
        return result

__all__ = [
    "InteractiveCopyEvidenceScenario",
    "assess_diagnostic_partial_copy",
    "build_interactive_copy_comparison",
]
