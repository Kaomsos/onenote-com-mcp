"""Pure contracts for D-stage per-capability interactive Copy evidence."""

from __future__ import annotations

import argparse
import asyncio

import pytest

from tests.manual_validation.mcp_stdio_client import COPY_NO_DELETE_POLICY, ClientFailure
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.runner import build_parser
from tests.manual_validation.scenarios.common import interactive_copy
from tests.manual_validation.scenarios.common.interactive_copy import (
    assess_diagnostic_partial_copy,
    build_interactive_copy_comparison,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.test_utils import (
    mathml_structure_projection,
    read_json,
    write_json,
)


def _detected(capability: str) -> dict:
    return {
        "capability": capability,
        "observed": {capability: 1},
        "unexpected": [],
        "passed": True,
    }


def test_copy_display_equation_has_no_interactive_specific_cli_flags() -> None:
    parsed = build_parser().parse_args(["copy-display-equation"])

    assert not hasattr(parsed, "capture_page_xml")
    assert not hasattr(parsed, "copy_chain_length")
    assert not hasattr(parsed, "interactive_timeout")


def _copy_report(
    capability: str,
    *,
    issue_code: str = "content_type_unverified",
    issue_action: str = "preserved_unverified",
    validated: bool = False,
) -> dict:
    return {
        "verified": True,
        "lossless": validated,
        "copy_contract_satisfied": validated,
        "skipped_content": [],
        "issues": (
            []
            if validated
            else [
                {
                    "code": issue_code,
                    "content_type": capability,
                    "action": issue_action,
                }
            ]
        ),
        "page_results": [
            {
                "equivalence": {
                    "equivalent": True,
                    "verification_tier": "strict_canonical",
                    "checks": {
                        "canonical_xml": True,
                        "visible_text": True,
                        "content_objects": True,
                        "binary_sha256": True,
                    },
                }
            }
        ],
    }


def _page_xml(
    *,
    title: str = "Synthetic",
    ink: str = "synthetic",
    position_x: str = "1",
    position_y: str = "2",
    width: str = "3",
    height: str = "4",
    shape_info: str = "",
) -> str:
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        f"<one:Title><one:OE><one:T>{title}</one:T></one:OE></one:Title>"
        f'<one:InkDrawing objectID="generated"><one:Position x="{position_x}" y="{position_y}" />'
        f'<one:Size width="{width}" height="{height}" />'
        f"{shape_info}<one:Ink>{ink}</one:Ink></one:InkDrawing>"
        "</one:Page>"
    )


@pytest.mark.parametrize(
    "scenario_name,bootstrap_name,capability",
    [
        (
            "interactive-copy-inserted-file",
            "bootstrap-inserted-file-fixture",
            "InsertedFile",
        ),
        (
            "interactive-copy-ink-drawing",
            "bootstrap-ink-drawing-fixture",
            "InkDrawing",
        ),
        (
            "interactive-copy-media-file",
            "bootstrap-media-file-fixture",
            "MediaFile",
        ),
        (
            "interactive-copy-ui-shape",
            "bootstrap-shape-fixture",
            "UIShape",
        ),
        (
            "interactive-copy-inline-equation",
            "bootstrap-inline-equation-fixture",
            "InlineEquation",
        ),
    ],
)
def test_copy_consumers_share_bootstrap_identity_and_never_get_delete(
    scenario_name: str,
    bootstrap_name: str,
    capability: str,
) -> None:
    scenario = SCENARIO_REGISTRY.get(scenario_name)
    bootstrap = SCENARIO_REGISTRY.get(bootstrap_name)

    assert scenario.fixture_recipe.cache_fingerprint == bootstrap.fixture_recipe.cache_fingerprint
    assert scenario.fixture_recipe.consumer_scenario is True
    assert scenario.included_in_all is False
    assert scenario.spec.policy == COPY_NO_DELETE_POLICY
    assert {"plan_copy", "copy_page"} <= scenario.spec.tool_allowlist
    assert not any(tool.startswith("delete_") for tool in scenario.spec.tool_allowlist)
    assert scenario.spec.execution_contract["capability"] == capability
    if scenario_name == "interactive-copy-media-file":
        assert "create_section" in scenario.spec.tool_allowlist
        assert scenario.spec.execution_contract["same_and_cross_section"] is True
    else:
        assert "create_section" not in scenario.spec.tool_allowlist
        assert scenario.spec.execution_contract["same_and_cross_section"] is False


@pytest.mark.parametrize(
    "scenario_name",
    [
        "interactive-copy-inserted-file",
        "interactive-copy-ink-drawing",
        "interactive-copy-media-file",
    ],
)
def test_machine_comparator_accepts_validated_contract_and_rejects_schema_or_fidelity_drift(
    scenario_name: str,
) -> None:
    recipe = SCENARIO_REGISTRY.get(scenario_name).fixture_recipe
    objects = (
        {"kind": recipe.capability, "media_type": "synthetic", "can_delete": True},
    )
    comparison = build_interactive_copy_comparison(
        recipe,
        source_report=_detected(recipe.capability),
        target_report=_detected(recipe.capability),
        source_objects=objects,
        target_objects=objects,
        copy_report=_copy_report(recipe.capability, validated=True),
        source_xml=_page_xml(),
        target_xml=_page_xml(),
    )
    assert comparison["passed"] is True
    assert comparison["checks"]["strict_equivalence_checks"] is True
    assert comparison["checks"]["issues_match_copy_fidelity_contract"] is True

    legacy_target = ({"type": recipe.capability},)
    legacy = build_interactive_copy_comparison(
        recipe,
        source_report=_detected(recipe.capability),
        target_report=_detected(recipe.capability),
        source_objects=objects,
        target_objects=legacy_target,
        copy_report=_copy_report(recipe.capability, validated=True),
        source_xml=_page_xml(),
        target_xml=_page_xml(),
    )
    assert legacy["passed"] is False
    assert legacy["object_comparison"]["checks"]["public_kind_schema"] is False

    unexpected_issue = build_interactive_copy_comparison(
        recipe,
        source_report=_detected(recipe.capability),
        target_report=_detected(recipe.capability),
        source_objects=objects,
        target_objects=objects,
        copy_report=_copy_report(
            recipe.capability,
            issue_code="unsupported_page_root",
        ),
        source_xml=_page_xml(),
        target_xml=_page_xml(),
    )
    assert unexpected_issue["passed"] is False

    binary_mismatch = build_interactive_copy_comparison(
        recipe,
        source_report=_detected(recipe.capability),
        target_report=_detected(recipe.capability),
        source_objects=objects,
        target_objects=objects,
        copy_report=_copy_report(recipe.capability, validated=True),
        source_xml=_page_xml(),
        target_xml=_page_xml(),
        binary_evidence={
            "source_payload_count": 1,
            "target_payload_count": 1,
            "source_sha256": ["source"],
            "target_sha256": ["target"],
            "equal": False,
        },
    )
    assert binary_mismatch["passed"] is False
    assert binary_mismatch["checks"]["direct_binary_sha256_equal"] is False


def _diagnostic_partial_result(*, source_touched: bool = False) -> dict:
    source_id = "source-page"
    target_id = "target-page"
    copy_report = _copy_report("InkDrawing")
    copy_report.update(
        verified=False,
        id_map={source_id: target_id},
    )
    equivalence = copy_report["page_results"][0]["equivalence"]
    equivalence.update(equivalent=False)
    equivalence["checks"]["canonical_xml"] = False
    copy_report["page_results"][0].update(
        source_page_id=source_id,
        target_page_id=target_id,
    )
    return {
        "code": "partial_failure",
        "partial": True,
        "complete": False,
        "failed_step": "verify_copy",
        "outcome": "copy_unverified",
        "source_deleted": False,
        "source_touched": source_touched,
        "source_untouched": not source_touched,
        "created_ids": [target_id],
        "resolved_target_ids": [target_id],
        "completed_steps": [
            {"operation": "create"},
            {"operation": "write_page_content"},
            {"operation": "reorder_pages"},
        ],
        "copy_report": copy_report,
        "destination": {"id": target_id},
    }


def test_diagnostic_partial_admission_is_exact_and_source_safe() -> None:
    admitted = assess_diagnostic_partial_copy(
        _diagnostic_partial_result(),
        source_id="source-page",
        capability="InkDrawing",
    )
    assert admitted["admitted"] is True
    assert all(admitted["gates"].values())

    touched = assess_diagnostic_partial_copy(
        _diagnostic_partial_result(source_touched=True),
        source_id="source-page",
        capability="InkDrawing",
    )
    assert touched["admitted"] is False
    assert touched["gates"]["source_untouched"] is False

    known_com = _diagnostic_partial_result()
    known_com["copy_report"]["issues"] = []
    known_com_admission = assess_diagnostic_partial_copy(
        known_com,
        source_id="source-page",
        capability="DisplayEquation",
        temporary_known_com_normalization=True,
    )
    assert known_com_admission["admitted"] is True
    assert known_com_admission["temporary_known_com_normalization"] is True

    wrong_capability = assess_diagnostic_partial_copy(
        known_com,
        source_id="source-page",
        capability="InlineEquation",
        temporary_known_com_normalization=True,
    )
    assert wrong_capability["admitted"] is False
    assert wrong_capability["gates"]["temporary_normalization_scope_valid"] is False


def test_ink_semantic_readback_accepts_unrelated_canonical_drift_but_not_ink_drift() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-copy-ink-drawing").fixture_recipe
    objects = ({"kind": "InkDrawing", "can_delete": True},)
    partial = _diagnostic_partial_result()["copy_report"]

    accepted = build_interactive_copy_comparison(
        recipe,
        source_report=_detected("InkDrawing"),
        target_report=_detected("InkDrawing"),
        source_objects=objects,
        target_objects=objects,
        copy_report=partial,
        source_xml=_page_xml(title="Source"),
        target_xml=_page_xml(title="Target"),
        diagnostic_partial_admitted=True,
    )
    assert accepted["passed"] is True
    assert accepted["production_copy"]["verified"] is False
    assert accepted["capability_readback"]["verification_tier"] == "semantic_ink_drawing"
    assert accepted["capability_readback"]["canonical_xml_observed"] is False
    assert accepted["checks"]["production_result_admitted_for_evidence"] is True

    rejected = build_interactive_copy_comparison(
        recipe,
        source_report=_detected("InkDrawing"),
        target_report=_detected("InkDrawing"),
        source_objects=objects,
        target_objects=objects,
        copy_report=partial,
        source_xml=_page_xml(ink="source"),
        target_xml=_page_xml(ink="target"),
        diagnostic_partial_admitted=True,
    )
    assert rejected["passed"] is False
    assert rejected["capability_readback"]["checks"][
        "ink_structure_and_data_projection"
    ] is False


def test_ink_geometry_uses_only_the_evidenced_bounded_numeric_tolerance() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-copy-ink-drawing").fixture_recipe
    objects = ({"kind": "InkDrawing", "can_delete": True},)
    partial = _diagnostic_partial_result()["copy_report"]
    common = {
        "source_report": _detected("InkDrawing"),
        "target_report": _detected("InkDrawing"),
        "source_objects": objects,
        "target_objects": objects,
        "copy_report": partial,
        "diagnostic_partial_admitted": True,
    }

    within = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(
            position_x="188.2204437255859",
            width="79.17166900634765",
        ),
        target_xml=_page_xml(
            position_x="188.2203979492187",
            width="79.17165374755859",
        ),
    )
    readback = within["capability_readback"]
    assert within["passed"] is True
    assert readback["exact_ink_projection_equal"] is False
    assert readback["checks"]["ink_structure_and_data_projection"] is True
    assert readback["checks"]["ink_geometry_within_tolerance"] is True
    assert readback["ink_projection_comparison"]["geometry_absolute_tolerance"] == "0.0001"
    assert readback["ink_projection_comparison"]["max_geometry_absolute_delta"] == (
        "0.0000457763672"
    )
    assert all(
        item["within_tolerance"]
        for item in readback["ink_projection_comparison"]["geometry_deltas"]
    )

    outside = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(position_x="1.0000"),
        target_xml=_page_xml(position_x="1.0002"),
    )
    assert outside["passed"] is False
    assert outside["capability_readback"]["checks"][
        "ink_geometry_within_tolerance"
    ] is False
    assert outside["capability_readback"]["ink_projection_comparison"][
        "mismatch_paths"
    ] == ["/InkDrawing[0]/child[0]@x#outside-tolerance"]

    non_numeric = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(position_x="not-a-number"),
        target_xml=_page_xml(position_x="1"),
    )
    assert non_numeric["passed"] is False
    assert non_numeric["capability_readback"]["ink_projection_comparison"][
        "geometry_within_tolerance"
    ] is False


def _shape_snapshot(*, anchor_points: int = 0, freehand: bool = False) -> dict:
    capability = "InkDrawing" if freehand else "UIShape"
    markers = {} if freehand else {"ShapeInfo": 1}
    if anchor_points:
        markers["AnchorPoint"] = anchor_points
    return {
        "notebook_id": "notebook-id",
        "items": [
            {
                "id": "canvas-page",
                "resource_type": "page",
                "section_id": "canvas-section",
            }
        ],
        "page_hashes": {"canvas-page": "hash"},
        "page_objects": {
            "canvas-page": [
                {"kind": "Outline"},
                {"kind": "OE"},
                {"kind": "InkDrawing", "can_delete": True},
            ]
        },
        "page_capability_projections": {
            "canvas-page": {
                "schema_version": 2,
                "capabilities": ["Outline", capability],
                "object_kind_counts": {"InkDrawing": 1, "OE": 1, "Outline": 1},
                "structural_marker_counts": markers,
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
    }


@pytest.mark.parametrize("anchor_points", [0, 2])
def test_shape_detector_requires_inkdrawing_plus_one_shape_info(
    anchor_points: int,
) -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-shape-fixture").fixture_recipe

    report = recipe.content_report(
        _shape_snapshot(anchor_points=anchor_points),
        "canvas-page",
    )

    assert report["passed"] is True
    assert report["observed"] == {"InkDrawing": 1}
    assert report["accepted_kinds"] == ["InkDrawing"]
    assert report["accepted_projected_capabilities"] == ["UIShape"]
    assert report["shape_info_count"] == 1
    assert report["anchor_point_count"] == anchor_points
    assert report["representation_status"] == "requested_composite_observed"
    assert report["template_publish_allowed"] is True


def test_shape_detector_rejects_plain_ink_and_invalid_marker_schema() -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-shape-fixture").fixture_recipe

    freehand = recipe.content_report(_shape_snapshot(freehand=True), "canvas-page")
    assert freehand["passed"] is False
    assert freehand["missing"] == ["UIShape"]
    assert "InkDrawing" in freehand["unexpected"]
    assert "structural-marker-count:ShapeInfo=0" in freehand["unexpected"]

    malformed = _shape_snapshot()
    malformed["page_capability_projections"]["canvas-page"].pop(
        "structural_marker_counts"
    )
    report = recipe.content_report(malformed, "canvas-page")
    assert report["passed"] is False
    assert "invalid-structural-marker-schema" in report["unexpected"]


def test_shape_semantic_readback_reuses_ink_tolerance_and_preserves_shape_markers() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-copy-ui-shape").fixture_recipe
    objects = ({"kind": "InkDrawing", "can_delete": True},)
    copy_report = _copy_report("UIShape")
    copy_report["verified"] = False
    copy_report["page_results"][0]["equivalence"]["equivalent"] = False
    copy_report["page_results"][0]["equivalence"]["checks"]["canonical_xml"] = False
    common = {
        "source_report": _detected("UIShape"),
        "target_report": _detected("UIShape"),
        "source_objects": objects,
        "target_objects": objects,
        "copy_report": copy_report,
        "diagnostic_partial_admitted": True,
    }
    rectangle = '<one:ShapeInfo shapeType="rectangle" />'

    accepted = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(
            position_x="188.2204437255859",
            width="79.17166900634765",
            shape_info=rectangle,
        ),
        target_xml=_page_xml(
            position_x="188.2203979492187",
            width="79.17165374755859",
            shape_info=rectangle,
        ),
    )
    assert accepted["passed"] is True
    readback = accepted["capability_readback"]
    assert readback["verification_tier"] == "semantic_ui_shape"
    assert readback["checks"]["ink_geometry_within_tolerance"] is True
    assert readback["checks"]["one_source_shape_info"] is True
    assert readback["checks"]["one_target_shape_info"] is True
    assert readback["checks"]["shape_marker_counts_equal"] is True

    observed_shape_quantization = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(
            position_x="220.0967254638672",
            position_y="190.4723052978516",
            width="216.8067321777344",
            height="190.5564117431641",
            shape_info=rectangle,
        ),
        target_xml=_page_xml(
            position_x="220.0834808349609",
            position_y="190.4891052246094",
            width="216.7936859130859",
            height="190.5732574462891",
            shape_info=rectangle,
        ),
    )
    shape_readback = observed_shape_quantization["capability_readback"]
    assert observed_shape_quantization["passed"] is True
    assert shape_readback["ink_projection_comparison"][
        "geometry_absolute_tolerance"
    ] == "0.02"
    assert shape_readback["ink_projection_comparison"][
        "max_geometry_absolute_delta"
    ] == "0.0168457031250"

    outside_shape_tolerance = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(position_x="1.0000", shape_info=rectangle),
        target_xml=_page_xml(position_x="1.0201", shape_info=rectangle),
    )
    assert outside_shape_tolerance["passed"] is False
    assert outside_shape_tolerance["capability_readback"]["checks"][
        "ink_geometry_within_tolerance"
    ] is False

    arrow = (
        '<one:ShapeInfo shapeType="arrow"><one:AnchorPoint/><one:AnchorPoint/>'
        "</one:ShapeInfo>"
    )
    changed_shape = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(shape_info=rectangle),
        target_xml=_page_xml(shape_info=arrow),
    )
    assert changed_shape["passed"] is False
    assert changed_shape["capability_readback"]["checks"][
        "shape_marker_counts_equal"
    ] is False

    lost_marker = build_interactive_copy_comparison(
        recipe,
        **common,
        source_xml=_page_xml(shape_info=rectangle),
        target_xml=_page_xml(),
    )
    assert lost_marker["passed"] is False
    assert lost_marker["capability_readback"]["checks"][
        "one_target_shape_info"
    ] is False


def _copy_snapshot(capability: str, *, include_target: bool) -> dict:
    source = {
        "id": "source-page",
        "resource_type": "page",
        "title": "01-Interactive-Canvas",
        "name": "01-Interactive-Canvas",
        "section_id": "canvas-section",
        "parent_id": "canvas-section",
        "page_level": 1,
        "parent_page_id": None,
        "order": 0,
    }
    items = [source]
    page_hashes = {"source-page": "source-hash"}
    page_objects = {
        "source-page": [
            {"kind": "Outline"},
            {"kind": "OE"},
            {"kind": capability, "can_delete": True},
        ]
    }
    projections = {
        "source-page": {
            "capabilities": ["Outline", capability],
            "object_kind_counts": {"Outline": 1, "OE": 1, capability: 1},
            "unknown_nodes": [],
            "unsupported_page_roots": [],
            "complete": True,
        }
    }
    if include_target:
        items.append(
            {
                **source,
                "id": "target-page",
                "title": "02-InkDrawing-Copy-recorded",
                "name": "02-InkDrawing-Copy-recorded",
                "order": 1,
            }
        )
        page_hashes["target-page"] = "target-hash"
        page_objects["target-page"] = [dict(value) for value in page_objects["source-page"]]
        projections["target-page"] = dict(projections["source-page"])
    return {
        "notebook_id": "notebook-id",
        "items": items,
        "page_hashes": page_hashes,
        "page_objects": page_objects,
        "page_capability_projections": projections,
    }


@pytest.mark.parametrize("diagnostic_partial", [False, True])
def test_interactive_copy_execute_persists_machine_and_human_evidence(
    monkeypatch, tmp_path, diagnostic_partial: bool
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-ink-drawing")
    before = _copy_snapshot("InkDrawing", include_target=False)
    after = _copy_snapshot("InkDrawing", include_target=True)
    snapshots = iter([before, after])

    async def fake_snapshot(_client, _notebook_id: str) -> dict:
        return next(snapshots)

    async def fake_plan(_client, _arguments, **_kwargs) -> dict:
        return {
            "plan_digest": "stable-plan",
            "include_descendants": False,
            "source": {
                **before["items"][0],
                "modified": "recorded-modified",
            },
            "content_capabilities": ["Outline", "InkDrawing"],
        }

    copy_report = _copy_report("InkDrawing")
    copy_report["id_map"] = {"source-page": "target-page"}
    copy_report["page_results"][0].update(
        source_page_id="source-page",
        target_page_id="target-page",
    )

    async def fake_copy(_client, tool: str, arguments: dict, evidence_path) -> dict:
        assert tool == "copy_page"
        assert arguments["page_id"] == "source-page"
        assert arguments["destination_section_id"] == "canvas-section"
        assert arguments["plan_digest"] == "stable-plan"
        assert arguments["include_descendants"] is False
        result = (
            _diagnostic_partial_result()
            if diagnostic_partial
            else {
                "item": after["items"][1],
                "copy_report": copy_report,
            }
        )
        write_json(evidence_path, result)
        if diagnostic_partial:
            raise ClientFailure("copy verification failed", envelope=result)
        return result

    async def fake_input(_prompt: str, _timeout: int) -> str:
        return f"ACCEPT {tmp_path.name} InkDrawing COPY"

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            assert name == "get_page_xml"
            page_id = arguments["page_id"]
            title = "Source" if page_id == "source-page" else "Target"
            return {
                "xml": (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
                    f"<one:Title><one:OE><one:T>{title}</one:T></one:OE></one:Title>"
                    '<one:InkDrawing objectID="generated"><one:Ink>synthetic</one:Ink></one:InkDrawing>'
                    "</one:Page>"
                )
            }

    monkeypatch.setattr(interactive_copy, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(interactive_copy, "stable_copy_plan", fake_plan)
    monkeypatch.setattr(interactive_copy, "call_with_result_evidence", fake_copy)
    monkeypatch.setattr(interactive_copy, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_copy, "run_safe_timestamp", lambda _args: "recorded")
    monkeypatch.setattr(interactive_copy, "page_binary_hashes", lambda _xml: ("sha256",))
    manifest = {
        "notebook": {"id": "notebook-id", "name": "Disposable"},
        "structure": {
            "canvas_section": {"id": "canvas-section"},
            "canvas_page": {"id": "source-page"},
        },
        "fixture_cache": {
            "template_instance_id": scenario.fixture_recipe.default_template_instance_id,
            "interactive_live_validation": {"passed": True},
        },
    }
    result = asyncio.run(
        scenario.execute(
            argparse.Namespace(
                notebook_name="Disposable",
                interactive_timeout=60,
                keep_worksite=False,
                run_identity=argparse.Namespace(safe_timestamp="recorded"),
            ),
            RuntimeOptions(tmp_path, 1_800, False, False, use_cache=True),
            manifest,
            client=Client(),
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert result["source_deleted"] is False
    assert result["diagnostic_partial_admitted"] is diagnostic_partial
    comparison = read_json(
        tmp_path / "scenarios" / "interactive-copy-ink-drawing" / "machine-comparison.json"
    )
    assert comparison["passed"] is True
    assert comparison["production_copy"]["binary_evidence"]["payloads_exposed"] is False
    assert comparison["production_copy"]["binary_evidence"]["payload_hashes_recorded"] is True
    assert comparison["checks"]["diagnostic_partial_admitted"] is diagnostic_partial
    admission_path = (
        tmp_path
        / "scenarios"
        / "interactive-copy-ink-drawing"
        / "partial-result-admission.json"
    )
    assert admission_path.exists() is diagnostic_partial
    structure = read_json(
        tmp_path
        / "scenarios"
        / "interactive-copy-ink-drawing"
        / "page-xml-structure.json"
    )
    assert structure["passed"] is True
    assert structure["content_exposed"] is False
    assert not (
        tmp_path
        / "scenarios"
        / "interactive-copy-ink-drawing"
        / "source-page.xml"
    ).exists()
    human = read_json(
        tmp_path / "scenarios" / "interactive-copy-ink-drawing" / "human-acceptance.json"
    )
    assert human["verdict"] == "accepted"


def test_rejected_partial_copy_captures_xml_structure_before_reraising(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-ink-drawing")
    before = _copy_snapshot("InkDrawing", include_target=False)

    async def fake_snapshot(_client, _notebook_id: str) -> dict:
        return before

    async def fake_plan(_client, _arguments, **_kwargs) -> dict:
        return {
            "plan_digest": "stable-plan",
            "include_descendants": False,
            "source": {**before["items"][0], "modified": "recorded-modified"},
            "content_capabilities": ["Outline", "InkDrawing"],
        }

    rejected = {
        "code": "partial_failure",
        "partial": True,
        "complete": False,
        "failed_step": "write_page_content",
        "outcome": "copy_unverified",
        "source_deleted": False,
        "source_touched": False,
        "source_untouched": True,
        "created_ids": ["target-page"],
        "resolved_target_ids": ["target-page"],
        "id_map": {"source-page": "target-page"},
        "completed_steps": [{"operation": "create"}],
    }

    async def fake_copy(_client, _tool, _arguments, evidence_path):
        write_json(evidence_path, rejected)
        raise ClientFailure("copy verification failed", envelope=rejected)

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            assert name == "get_page_xml"
            title = "Source" if arguments["page_id"] == "source-page" else "Target"
            return {
                "xml": (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
                    f"<one:Title><one:OE><one:T>{title}</one:T></one:OE></one:Title>"
                    f"<one:Outline><one:OEChildren><one:OE><one:T>{title} body</one:T>"
                    "<one:Image><one:Data>c2Vuc2l0aXZlLWJpbmFyeQ==</one:Data>"
                    "</one:Image></one:OE></one:OEChildren></one:Outline>"
                    "</one:Page>"
                )
            }

    monkeypatch.setattr(interactive_copy, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(interactive_copy, "stable_copy_plan", fake_plan)
    monkeypatch.setattr(interactive_copy, "call_with_result_evidence", fake_copy)
    monkeypatch.setattr(interactive_copy, "run_safe_timestamp", lambda _args: "recorded")
    manifest = {
        "notebook": {"id": "notebook-id", "name": "Disposable"},
        "structure": {
            "canvas_section": {"id": "canvas-section"},
            "canvas_page": {"id": "source-page"},
        },
        "fixture_cache": {
            "template_instance_id": scenario.fixture_recipe.default_template_instance_id,
            "interactive_live_validation": {"passed": True},
        },
    }

    with pytest.raises(ClientFailure):
        asyncio.run(
            scenario.execute(
                argparse.Namespace(
                    notebook_name="Disposable",
                    interactive_timeout=60,
                    keep_worksite=False,
                    capture_page_xml=True,
                    run_identity=argparse.Namespace(safe_timestamp="recorded"),
                ),
                RuntimeOptions(tmp_path, 1_800, False, False, use_cache=True),
                manifest,
                client=Client(),
                fixture_result={},
            )
        )

    structure = read_json(
        tmp_path
        / "scenarios"
        / "interactive-copy-ink-drawing"
        / "page-xml-structure.json"
    )
    assert structure["passed"] is True
    assert structure["source_page_id"] == "source-page"
    assert structure["target_page_id"] == "target-page"
    assert structure["content_exposed"] is False
    evidence_dir = (
        tmp_path / "scenarios" / "interactive-copy-ink-drawing"
    )
    source_xml = (evidence_dir / "source-page.xml").read_text(encoding="utf-8")
    target_xml = (evidence_dir / "target-page.xml").read_text(encoding="utf-8")
    assert "Source body" in source_xml
    assert "Target body" in target_xml
    assert "c2Vuc2l0aXZlLWJpbmFyeQ==" not in source_xml
    assert "binary-data-redacted" in source_xml
    sensitive = read_json(evidence_dir / "sensitive-evidence.json")
    assert sensitive["opt_in_argument"] == "--capture-page-xml"
    assert sensitive["contains_user_authored_body_text"] is True
    assert sensitive["source"]["binary_payload_count"] == 1
    assert sensitive["source"]["binary_data_retained"] is False


def _legacy_display_equation_copy_chain_keeps_one_known_com_break_bounded(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("copy-display-equation")
    one = "http://schemas.microsoft.com/office/onenote/2013/onenote"
    math = "http://www.w3.org/1998/Math/MathML"

    def page_xml(*, break_count: int) -> str:
        prefix = (
            "&lt;span style='font-family:Calibri' lang=zh-CN&gt;"
            + "&lt;br /&gt;" * break_count
            + "&lt;/span&gt;"
            if break_count
            else ""
        )
        return (
            f'<one:Page xmlns:one="{one}">'
            "<one:Title><one:OE><one:T>Display</one:T></one:OE></one:Title>"
            "<one:Outline><one:OEChildren><one:OE><one:T>base</one:T></one:OE>"
            f"<one:OE><one:T>{prefix}&lt;math xmlns=\"{math}\" display=\"block\"&gt;"
            "&lt;mi&gt;x&lt;/mi&gt;&lt;/math&gt;</one:T></one:OE>"
            "</one:OEChildren></one:Outline></one:Page>"
        )

    xml_by_id = {
        "source-page": page_xml(break_count=1),
        "target-1": page_xml(break_count=1),
        "target-2": page_xml(break_count=1),
    }

    def snapshot(page_ids: list[str]) -> dict:
        items = [
            {
                "id": page_id,
                "resource_type": "page",
                "title": (
                    "01-Source-Parent"
                    if page_id == "source-page"
                    else (
                        "02-DisplayEquation-Copy-recorded"
                        if page_id == "target-1"
                        else "03-DisplayEquation-Copy-Chain-2-recorded"
                    )
                ),
                "name": page_id,
                "section_id": "canvas-section",
                "parent_id": "canvas-section",
                "order": page_ids.index(page_id),
                "page_level": 1,
                "modified": f"modified-{page_id}",
            }
            for page_id in page_ids
        ]
        objects = [
            {"kind": "Outline", "can_delete": True},
            {"kind": "OE", "can_delete": False},
            {"kind": "Table", "can_delete": False},
            {"kind": "Row", "can_delete": False},
            {"kind": "Cell", "can_delete": False},
            {"kind": "Image", "can_delete": False},
        ]
        return {
            "notebook_id": "notebook-id",
            "items": items,
            "page_hashes": {page_id: f"hash-{page_id}" for page_id in page_ids},
            "page_objects": {
                page_id: [dict(value) for value in objects] for page_id in page_ids
            },
            "page_capability_projections": {
                page_id: {
                    "capabilities": [
                        "DisplayEquation",
                        "Image",
                        "Outline",
                        "RichText",
                        "Table",
                    ],
                    "unknown_nodes": [],
                    "unsupported_page_roots": [],
                    "complete": True,
                }
                for page_id in page_ids
            },
            "page_mathml_structure_projections": {
                page_id: mathml_structure_projection(xml_by_id[page_id])
                for page_id in page_ids
            },
        }

    snapshots = iter(
        [
            snapshot(["source-page"]),
            snapshot(["source-page", "target-1"]),
            snapshot(["source-page", "target-1", "target-2"]),
        ]
    )

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    async def fake_plan(_client, arguments, **_kwargs):
        source_id = arguments["source_id"]
        return {
            "plan_digest": f"plan-{source_id}",
            "include_descendants": False,
            "source": {
                "id": source_id,
                "modified": f"modified-{source_id}",
            },
            "content_capabilities": [
                "DisplayEquation",
                "Image",
                "Outline",
                "RichText",
                "Table",
            ],
        }

    copy_number = 0

    async def fake_copy(_client, _tool, arguments, evidence_path):
        nonlocal copy_number
        copy_number += 1
        source_id = arguments["page_id"]
        target_id = f"target-{copy_number}"
        report = {
            "verified": True,
            "lossless": True,
            "issues": [],
            "skipped_content": [],
            "id_map": {source_id: target_id},
            "page_results": [
                {
                    "source_page_id": source_id,
                    "target_page_id": target_id,
                    "equivalence": {
                        "equivalent": True,
                        "checks": {
                            "canonical_xml": False,
                            "visible_text": True,
                            "content_objects": True,
                            "binary_sha256": True,
                            "semantic_mathml": True,
                            "display_equation_com_normalization": True,
                            "outside_mathml_canonical": True,
                        },
                    },
                }
            ],
        }
        result = {
            "partial": False,
            "created_ids": [target_id],
            "resolved_target_ids": [target_id],
            "copy_report": report,
            "item": {
                "id": target_id,
                "resource_type": "page",
                "title": arguments["destination_title"],
                "section_id": "canvas-section",
            },
        }
        write_json(evidence_path, result)
        return result

    async def fake_input(_prompt, _timeout):
        return f"ACCEPT {tmp_path.name} DisplayEquation COPY"

    class Client:
        async def call_tool(self, name, arguments):
            assert name == "get_page_xml"
            return {"xml": xml_by_id[arguments["page_id"]]}

    monkeypatch.setattr(interactive_copy, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(interactive_copy, "stable_copy_plan", fake_plan)
    monkeypatch.setattr(interactive_copy, "call_with_result_evidence", fake_copy)
    monkeypatch.setattr(interactive_copy, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_copy, "run_safe_timestamp", lambda _args: "recorded")
    monkeypatch.setattr(interactive_copy, "page_binary_hashes", lambda _xml: ())
    monkeypatch.setattr(interactive_copy, "assert_copy_mapping", lambda *_a, **_k: None)
    monkeypatch.setattr(interactive_copy, "assert_pages_unchanged", lambda *_a, **_k: None)

    result = asyncio.run(
        scenario.execute(
            argparse.Namespace(
                notebook_name="Disposable",
                interactive_timeout=60,
                copy_chain_length=2,
                capture_page_xml=False,
                keep_worksite=True,
                run_identity=argparse.Namespace(safe_timestamp="recorded"),
            ),
            RuntimeOptions(tmp_path, 1_800, False, False, use_cache=True),
            {
                "notebook": {"id": "notebook-id", "name": "Disposable"},
                "structure": {
                    "canvas_section": {"id": "canvas-section"},
                    "canvas_page": {"id": "source-page"},
                },
                "fixture_cache": {
                    "template_instance_id": (
                        scenario.fixture_recipe.default_template_instance_id
                    ),
                    "interactive_live_validation": {"passed": True},
                },
            },
            client=Client(),
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert result["copy_chain_length"] == 2
    assert result["target_ids"] == ["target-1", "target-2"]
    chain = read_json(
        tmp_path
        / "scenarios"
        / "copy-display-equation"
        / "copy-chain.json"
    )
    assert [hop["display_break_observation"]["target_count"] for hop in chain["hops"]] == [1, 1]
    assert [hop["display_break_observation"]["delta"] for hop in chain["hops"]] == [0, 0]


def test_media_copy_executes_same_and_cross_section_cases_in_one_run(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-media-file")
    source_section = {
        "id": "canvas-section",
        "resource_type": "section",
        "name": "00-MediaFile-Canvas",
        "parent_id": "notebook-id",
        "notebook_id": "notebook-id",
    }
    destination_section = {
        "id": "destination-section",
        "resource_type": "section",
        "name": "01-MediaFile-Cross-Section-recorded",
        "parent_id": "notebook-id",
        "notebook_id": "notebook-id",
    }

    def snapshot(*, same_target: bool, destination: bool, cross_target: bool) -> dict:
        value = _copy_snapshot("MediaFile", include_target=False)
        value["items"].insert(0, dict(source_section))
        targets = []
        if same_target:
            targets.append(
                {
                    **value["items"][1],
                    "id": "same-target",
                    "title": "02-MediaFile-Copy-recorded",
                    "name": "02-MediaFile-Copy-recorded",
                    "order": 1,
                }
            )
        if destination:
            value["items"].append(dict(destination_section))
        if cross_target:
            targets.append(
                {
                    **value["items"][1],
                    "id": "cross-target",
                    "title": "03-MediaFile-Cross-Section-Copy-recorded",
                    "name": "03-MediaFile-Cross-Section-Copy-recorded",
                    "section_id": "destination-section",
                    "parent_id": "destination-section",
                    "order": 0,
                }
            )
        for target in targets:
            value["items"].append(target)
            value["page_hashes"][target["id"]] = f"hash-{target['id']}"
            value["page_objects"][target["id"]] = [
                dict(item) for item in value["page_objects"]["source-page"]
            ]
            value["page_capability_projections"][target["id"]] = dict(
                value["page_capability_projections"]["source-page"]
            )
        return value

    before = snapshot(same_target=False, destination=False, cross_target=False)
    after_same = snapshot(same_target=True, destination=False, cross_target=False)
    before_cross = snapshot(same_target=True, destination=True, cross_target=False)
    after_cross = snapshot(same_target=True, destination=True, cross_target=True)
    snapshots = iter([before, after_same, before_cross, after_cross])

    async def fake_snapshot(_client, _notebook_id: str) -> dict:
        return next(snapshots)

    plan_destinations = []

    async def fake_plan(_client, arguments, **_kwargs) -> dict:
        plan_destinations.append(arguments["destination_parent_id"])
        return {
            "plan_digest": f"plan-{arguments['destination_parent_id']}",
            "include_descendants": False,
            "source": {
                **next(item for item in before["items"] if item.get("id") == "source-page"),
                "modified": "recorded-modified",
            },
            "content_capabilities": ["Outline", "MediaFile"],
        }

    calls = []

    async def fake_call(_client, tool: str, arguments: dict, evidence_path) -> dict:
        calls.append(tool)
        if tool == "create_section":
            result = {"section": dict(destination_section)}
        else:
            assert tool == "copy_page"
            target_id = (
                "same-target"
                if arguments["destination_section_id"] == "canvas-section"
                else "cross-target"
            )
            target_snapshot = after_same if target_id == "same-target" else after_cross
            target = next(
                item for item in target_snapshot["items"] if item.get("id") == target_id
            )
            report = _copy_report("MediaFile")
            report["id_map"] = {"source-page": target_id}
            report["page_results"][0].update(
                source_page_id="source-page",
                target_page_id=target_id,
            )
            result = {"item": target, "copy_report": report}
        write_json(evidence_path, result)
        return result

    async def fake_input(prompt: str, _timeout: int) -> str:
        assert "same-section target" in prompt
        assert "cross-section target" in prompt
        return f"ACCEPT {tmp_path.name} MediaFile COPY"

    media_xml = (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        "<one:Title><one:OE><one:T>Media</one:T></one:OE></one:Title>"
        "<one:Outline><one:OEChildren><one:OE><one:MediaIndex/>"
        "<one:MediaFile><one:MediaReference/></one:MediaFile></one:OE>"
        "</one:OEChildren></one:Outline><one:MediaPlaylist><one:MediaReference/>"
        "</one:MediaPlaylist></one:Page>"
    )

    class Client:
        async def call_tool(self, name: str, _arguments: dict) -> dict:
            assert name == "get_page_xml"
            return {"xml": media_xml}

    monkeypatch.setattr(interactive_copy, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(interactive_copy, "stable_copy_plan", fake_plan)
    monkeypatch.setattr(interactive_copy, "call_with_result_evidence", fake_call)
    monkeypatch.setattr(interactive_copy, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_copy, "run_safe_timestamp", lambda _args: "recorded")
    manifest = {
        "notebook": {"id": "notebook-id", "name": "Disposable"},
        "structure": {
            "canvas_section": {"id": "canvas-section"},
            "canvas_page": {"id": "source-page"},
        },
        "fixture_cache": {
            "template_instance_id": scenario.fixture_recipe.default_template_instance_id,
            "interactive_live_validation": {"passed": True},
        },
    }
    result = asyncio.run(
        scenario.execute(
            argparse.Namespace(
                notebook_name="Disposable",
                interactive_timeout=60,
                keep_worksite=False,
                run_identity=argparse.Namespace(safe_timestamp="recorded"),
            ),
            RuntimeOptions(tmp_path, 1_800, False, False, use_cache=True),
            manifest,
            client=Client(),
            fixture_result={},
        )
    )

    assert calls == ["copy_page", "create_section", "copy_page"]
    assert plan_destinations == ["canvas-section", "destination-section"]
    assert result["target_ids"] == ["same-target", "cross-target"]
    assert result["same_section_validated"] is True
    assert result["cross_section_validated"] is True
    assert [case["case"] for case in result["cases"]] == [
        "same-section",
        "cross-section",
    ]
    assert read_json(
        tmp_path
        / "scenarios"
        / "interactive-copy-media-file"
        / "destination-section.json"
    )["distinct"] is True
    assert read_json(
        tmp_path
        / "scenarios"
        / "interactive-copy-media-file"
        / "human-acceptance.json"
    )["target_ids"] == ["same-target", "cross-target"]
